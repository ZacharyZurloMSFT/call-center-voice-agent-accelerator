"""Handles media streaming to Azure Voice Live API via WebSocket for web clients."""

import asyncio
import base64
import json
import logging
import uuid
import os
from datetime import datetime
from urllib.parse import quote

from websockets.asyncio.client import connect as ws_connect
from websockets.typing import Data
from ..functions import get_function_definitions, handle_function_call
from ..cosmos_client import ConversationCosmosClient

logger = logging.getLogger(__name__)


def session_config():
    """Returns the default session configuration for Voice Live."""
    return {
        "type": "session.update",
        "session": {
            "instructions": "You are a helpful customer service AI assistant for an e-commerce company. You can help customers check their order status. Always respond in English regardless of the user's input language. Be friendly, concise, and helpful. When customers ask about their orders, use the check_order_status function to look up their order information. Wait for the customer to speak first before responding.",
            "turn_detection": {
                "type": "azure_semantic_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 800,
                "remove_filler_words": False,
            },
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "voice": {
                "name": "en-US-MultiTalker-Ava-Andrew:DragonHDv1.2Neural",
                "type": "azure-standard",
                "temperature": 0.8,
            },
            "tools": [
                {
                    "type": "function", 
                    **func_def  # Spread the function definition directly
                } for func_def in get_function_definitions()
            ]
        },
        "event_id": ""
    }


class ACSMediaHandler:
    """Manages audio streaming between web clients and Azure Voice Live API."""

    def __init__(self, config):
        self.endpoint = config["AZURE_VOICE_LIVE_ENDPOINT"]
        self.model = config["VOICE_LIVE_MODEL"]
        self.api_key = config["AZURE_VOICE_LIVE_API_KEY"]
        # self.client_id = config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "")
        self.send_queue = asyncio.Queue()
        self.ws = None
        self.send_task = None
        self.incoming_websocket = None
        self.is_raw_audio = True
        # Track a session id for grouping transcripts
        self.session_id = None
        # Keep config and a simple in-memory buffer of transcripts for the active session
        self.config = config
        self.transcripts = []
        # Initialize Cosmos DB client
        self.cosmos_client = ConversationCosmosClient(config)
        # Track if a response.create request is already in flight
        self._response_in_progress = False

    def _generate_guid(self):
        return str(uuid.uuid4())

    async def connect(self):
        """Connects to Azure Voice Live API via WebSocket using API key authentication.

        This method uses the configured AZURE_VOICE_LIVE_API_KEY and VOICE_LIVE_MODEL
        to connect to the Voice Live realtime endpoint. The previous agent/token-based
        flow and Semantic Kernel integration have been removed.
        """
        api_version = os.getenv("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")

        # Validate configuration
        if not self.endpoint:
            raise ValueError("AZURE_VOICE_LIVE_ENDPOINT is required")

        if not self.api_key:
            raise ValueError("AZURE_VOICE_LIVE_API_KEY is required for API key authentication")

        # Build WebSocket URL for realtime endpoint and specify model via query parameter
        base_ws = self.endpoint.rstrip("/").replace("https://", "wss://")
        url = (
            f"{base_ws}/voice-live/realtime?api-version={api_version}"
            f"&model={quote(self.model)}"
        )

        # Use API key header (voice_live_test_client.py uses 'api-key')
        headers = {
            "x-ms-client-request-id": self._generate_guid(),
            "api-key": self.api_key,
        }

        # Establish websocket connection
        self.ws = await ws_connect(url, additional_headers=headers)
        logger.info("[VoiceLiveACSHandler] Connected to Voice Live API using API key auth")

        await self._send_json(session_config())
        # Don't send response.create immediately - wait for user input

        asyncio.create_task(self._receiver_loop())
        self.send_task = asyncio.create_task(self._sender_loop())

    async def init_incoming_websocket(self, socket, is_raw_audio=True):
        """Sets up incoming ACS WebSocket."""
        self.incoming_websocket = socket
        self.is_raw_audio = is_raw_audio

    async def audio_to_voicelive(self, audio_b64: str):
        """Queues audio data to be sent to Voice Live API."""
        await self.send_queue.put(
            json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64})
        )

    async def _send_json(self, obj):
        """Sends a JSON object over WebSocket."""
        if self.ws:
            await self.ws.send(json.dumps(obj))

    async def _maybe_request_response(self, reason: str):
        """Request a model response if one is not already in flight."""
        if self._response_in_progress:
            logger.debug(
                "[VoiceLiveACSHandler] Skip response.create (%s); response already in progress",
                reason,
            )
            return

        await self._send_json({"type": "response.create"})
        self._response_in_progress = True
        logger.debug("[VoiceLiveACSHandler] response.create sent (%s)", reason)

    async def _sender_loop(self):
        """Continuously sends messages from the queue to the Voice Live WebSocket."""
        try:
            while True:
                msg = await self.send_queue.get()
                if self.ws:
                    await self.ws.send(msg)
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Sender loop error")

    async def _receiver_loop(self):
        """Handles incoming events from the Voice Live WebSocket."""
        try:
            async for message in self.ws:
                try:
                    event = json.loads(message)
                    event_type = event.get("type")

                    match event_type:
                        case "session.created":
                            session_id = event.get("session", {}).get("id")
                            logger.info("[VoiceLiveACSHandler] Session ID: %s", session_id)
                            # store a session id to associate transcripts
                            self.session_id = session_id

                        case "input_audio_buffer.cleared":
                            logger.info("Input Audio Buffer Cleared Message")

                        case "input_audio_buffer.speech_started":
                            logger.info(
                                "Voice activity detection started at %s ms",
                                event.get("audio_start_ms"),
                            )
                            await self.stop_audio()

                        case "input_audio_buffer.speech_stopped":
                            logger.info("Speech stopped")
                            # Trigger response generation after user stops speaking
                            await self._maybe_request_response("speech_stopped")

                        case "conversation.item.input_audio_transcription.completed":
                            transcript = event.get("transcript")
                            logger.info("User: %s", transcript)
                            try:
                                entry = {
                                    "timestamp": datetime.utcnow().isoformat(),
                                    "session_id": self.session_id or self._generate_guid(),
                                    "role": "user",
                                    "text": transcript,
                                }
                                self.transcripts.append(entry)
                            except Exception:
                                logger.exception("Failed to buffer user transcript")

                        case "conversation.item.input_audio_transcription.failed":
                            error_msg = event.get("error")
                            logger.warning("Transcription Error: %s", error_msg)

                        case "response.created":
                            response_id = event.get("response", {}).get("id")
                            logger.info("Response created: %s", response_id)
                            self._response_in_progress = True

                        case "response.done":
                            response = event.get("response", {})
                            logger.info("Response Done: Id=%s", response.get("id"))
                            self._response_in_progress = False
                            if response.get("status_details"):
                                logger.info(
                                    "Status Details: %s",
                                    json.dumps(response["status_details"], indent=2),
                                )

                        case "response.failed":
                            response = event.get("response", {})
                            logger.warning("Response failed: %s", response)
                            self._response_in_progress = False

                        case "response.text.delta":
                            text = event.get("text", "")
                            logger.info("Response Text Delta: %s", text)

                        case "response.text.done":
                            text = event.get("text", "")
                            logger.info("Response Text Done: %s", text)
                            # Buffer assistant response transcript
                            try:
                                entry = {
                                    "timestamp": datetime.utcnow().isoformat(),
                                    "session_id": self.session_id or self._generate_guid(),
                                    "role": "assistant",
                                    "text": text,
                                }
                                self.transcripts.append(entry)
                            except Exception:
                                logger.exception("Failed to buffer assistant transcript")

                        case "response.output_item.added":
                            item = event.get("item", {})
                            item_type = item.get("type")
                            logger.info("Output item added: %s", item_type)
                            if item_type == "function_call":
                                function_name = item.get("name")
                                logger.info("Function call item added: %s", function_name)

                        case "response.output_item.done":
                            item = event.get("item", {})
                            item_type = item.get("type")
                            if item_type == "function_call":
                                call_id = item.get("call_id")
                                function_name = item.get("name")
                                arguments = item.get("arguments")
                                logger.info("Function call item done: %s (call_id: %s) with args: %s", function_name, call_id, arguments)
                                
                                # Handle the function call
                                try:
                                    args_dict = json.loads(arguments) if arguments else {}
                                    result = await handle_function_call(function_name, args_dict)
                                    
                                    # Send function call result back to Voice Live
                                    function_result = {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": result
                                        }
                                    }
                                    await self._send_json(function_result)
                                    
                                    # Trigger response generation
                                    await self._maybe_request_response("function_call_complete")
                                    
                                except Exception as e:
                                    logger.exception("Error handling function call: %s", e)
                                    # Send error result
                                    error_result = {
                                        "type": "conversation.item.create", 
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": f"Error processing request: {str(e)}"
                                        }
                                    }
                                    await self._send_json(error_result)
                                    await self._maybe_request_response("function_call_error")

                        case "response.function_call_arguments.delta":
                            arguments = event.get("delta", "")
                            logger.info("Function call arguments delta: %s", arguments)

                        case "response.function_call_arguments.done":
                            item_id = event.get("item_id")
                            name = event.get("name")
                            arguments = event.get("arguments")
                            call_id = event.get("call_id")
                            logger.info("Function call arguments done: %s (call_id: %s) with args: %s", name, call_id, arguments)
                            
                            # This event might be redundant with response.output_item.done, 
                            # but we'll handle it just in case
                            if not call_id:
                                logger.warning("No call_id in function_call_arguments.done event")
                                return

                        case "response.audio.delta":
                            # convert to acs audio and send to client
                            try:
                                base64_audio = event.get("delta")
                                if base64_audio:
                                    logger.info(
                                        "[VoiceLiveACSHandler] Audio delta event: %s",
                                        json.dumps(event),
                                    )
                                    await self.voicelive_to_acs(base64_audio)
                            except Exception:
                                logger.exception("Error handling audio delta")

                        case "error":
                            error_info = event.get("error")
                            logger.error("Voice Live Error: %s", error_info)
                            if isinstance(error_info, dict) and error_info.get("code") in {
                                "conversation_already_has_active_response",
                                "speech_synthesis_error",
                            }:
                                self._response_in_progress = False

                        case _:
                            logger.debug("Unhandled event type: %s", event_type)
                except json.JSONDecodeError:
                    logger.debug("Received non-json message")
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Receiver loop error")

    async def send_message(self, message: Data):
        """Sends data back to client WebSocket."""
        try:
            if self.incoming_websocket:
                await self.incoming_websocket.send(message)
        except Exception:
            logger.exception("Failed to send message to incoming websocket")

    async def voicelive_to_acs(self, base64_data):
        """Converts Voice Live audio delta to ACS audio message."""
        try:
            # base64 audio delta from Voice Live is 16-bit PCM little-endian
            audio_bytes = base64.b64decode(base64_data)
            if not self.is_raw_audio:
                # If expecting non-raw audio format, wrap in appropriate ACS message (not implemented)
                pass
            # Send raw audio bytes to ACS/Web client
            await self.send_message(audio_bytes)
        except Exception:
            logger.exception("Failed to convert Voice Live audio to ACS message")

    async def stop_audio(self):
        """Sends a StopAudio signal to client."""
        try:
            if self.incoming_websocket:
                await self.incoming_websocket.send(json.dumps({"type": "StopAudio"}))
        except Exception:
            logger.exception("Failed to stop audio to incoming websocket")

    async def web_to_voicelive(self, audio_bytes):
        """Encodes raw audio bytes and sends to Voice Live API."""
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        await self.audio_to_voicelive(audio_b64)

    async def upload_transcript(self) -> bool:
        """Upload transcripts to Cosmos DB (if configured)."""
        try:
            if not self.cosmos_client.is_available():
                logger.info("Transcript upload skipped because Cosmos DB is not configured")
                return False

            if not self.transcripts:
                logger.info("No transcripts to upload for session %s", self.session_id or "unknown")
                return True

            session_id = self.session_id or self._generate_guid()
            success = await self.cosmos_client.store_conversation(session_id, self.transcripts)
            
            if success:
                logger.info("Transcripts successfully uploaded to Cosmos DB for session %s", session_id)
            else:
                logger.warning("Failed to upload transcripts to Cosmos DB for session %s", session_id)
                
            return success
        except Exception:
            logger.exception("Failed to upload transcripts to Cosmos DB")
            return False
