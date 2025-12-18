"""Handles media streaming to Azure Voice Live API via WebSocket for web clients."""

import asyncio
import base64
import json
import logging
import uuid
import os
import re
from datetime import datetime
from urllib.parse import quote
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape as xml_escape

from websockets.asyncio.client import connect as ws_connect
from websockets.typing import Data
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ClientAuthenticationError
from ..functions import (
    build_patient_context,
    get_function_definitions,
    get_patient_profile,
    handle_function_call,
)
from ..cosmos_client import ConversationCosmosClient

logger = logging.getLogger(__name__)


_VOICE_LIVE_AAD_SCOPES = (
    "https://ai.azure.com/.default",
    "https://cognitiveservices.azure.com/.default",
)


_EMAIL_REGEX = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)

_EMAIL_PUNCT_ALIAS: Dict[str, str] = {
    "_": "underscore",
    ".": "dot",
    "-": "hyphen",
    "@": "at",
}


# Explicit letter-name aliases to avoid voices expanding letters into
# "A as in apple" style disambiguation.
# Note: These are short, commonly understood pronunciations for US English.
_LETTER_NAME_ALIAS: Dict[str, str] = {
    "A": "ay",
    "B": "bee",
    "C": "see",
    "D": "dee",
    "E": "ee",
    "F": "eff",
    "G": "gee",
    "H": "aitch",
    "I": "eye",
    "J": "jay",
    "K": "kay",
    "L": "el",
    "M": "em",
    "N": "en",
    "O": "oh",
    "P": "pee",
    "Q": "cue",
    "R": "ar",
    "S": "ess",
    "T": "tee",
    "U": "you",
    "V": "vee",
    "W": "double u",
    "X": "ex",
    "Y": "why",
    "Z": "zee",
}


def _email_to_spoken_tokens(email: str) -> str:
    """Return a safe, punctuation-spoken, character-spaced fallback string."""

    tokens: list[str] = []
    for ch in email:
        if ch.isalnum():
            tokens.append(ch)
            continue

        alias = _EMAIL_PUNCT_ALIAS.get(ch)
        if alias:
            tokens.append(alias)
        else:
            tokens.append(ch)

    # Use commas to force clearer separation of adjacent letter names.
    # Example: "ACE" -> "A, C, E" (more reliable than "A C E" for some voices).
    return ", ".join(tokens)


def _email_to_ssml(email: str, *, break_ms: int = 200) -> str:
    """Convert an email to SSML that spells characters and speaks punctuation.

    Uses only tags commonly supported by OpenAI voices in Azure Speech:
    speak, say-as, sub, break.
    """

    normalized = (email or "").strip()
    if not normalized:
        return "<speak></speak>"

    # Use a slightly shorter break between letters than between punctuation tokens.
    # This prevents coarticulation where adjacent letter names can merge
    # (e.g., "C" ("see") followed by "E" ("ee") sounding like only "C").
    token_break_ms = int(break_ms)
    letter_break_ms = max(120, min(200, token_break_ms - 40))
    token_break_tag = f'<break time="{token_break_ms}ms"/>'
    letter_break_tag = f'<break time="{letter_break_ms}ms"/>'

    parts: list[str] = ["<speak>"]

    for ch in normalized:
        # Letters: force the spoken form explicitly via <sub alias="..."> to avoid
        # voices choosing expanded disambiguations like "A as in apple".
        if ch.isalpha():
            alias = _LETTER_NAME_ALIAS.get(ch.upper())
            if alias:
                parts.append(
                    f'<sub alias="{xml_escape(alias)}">{xml_escape(ch)}</sub>'
                )
            else:
                parts.append(
                    f'<say-as interpret-as="characters">{xml_escape(ch)}</say-as>'
                )
            parts.append(letter_break_tag)
            continue

        # Digits: characters mode is typically read as digit names without the
        # "as in" expansion.
        if ch.isdigit():
            parts.append(
                f'<say-as interpret-as="characters">{xml_escape(ch)}</say-as>'
            )
            parts.append(letter_break_tag)
            continue

        alias = _EMAIL_PUNCT_ALIAS.get(ch)
        if alias:
            # Keep the original character as the visible form, speak the alias.
            parts.append(f'<sub alias="{xml_escape(alias)}">{xml_escape(ch)}</sub>')
        else:
            # For unexpected characters, still force character spelling.
            parts.append(f'<say-as interpret-as="characters">{xml_escape(ch)}</say-as>')
        parts.append(token_break_tag)

    # Trim trailing break for cleaner speech.
    if parts and parts[-1] in {token_break_tag, letter_break_tag}:
        parts.pop()

    parts.append("</speak>")
    return "".join(parts)

# Track active ACS media handlers by Voice Live session id so background services
# can direct tool invocations to the correct conversation.
_ACTIVE_HANDLERS: Dict[str, "ACSMediaHandler"] = {}


def _register_handler(session_id: str, handler: "ACSMediaHandler") -> None:
    _ACTIVE_HANDLERS[session_id] = handler


def _unregister_handler(session_id: Optional[str]) -> None:
    if session_id:
        _ACTIVE_HANDLERS.pop(session_id, None)


TELEHEALTH_BASE_PROMPT = (
    "You are a compassionate virtual care coordinator supporting tele-health visits. "
    "Confirm patient identity before sharing private details, keep explanations clear, and "
    "offer to connect the patient with clinical staff when questions fall outside your scope. "
    "You can schedule tele-health or in-clinic appointments, summarize recent care activity, "
    "and submit prescription refill requests using the available tools."
)


def compose_instructions(patient_context: Optional[str] = None) -> str:
    """Return the base tele-health instructions (patient data is injected separately)."""

    return TELEHEALTH_BASE_PROMPT


def session_config(voice_name: str | None = None):
    """Returns the default session configuration for Voice Live."""
    voice_name = voice_name or "en-US-Ava:DragonHDLatestNeural"
    return {
        "type": "session.update",
        "session": {
            "instructions": compose_instructions(),
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
                "name": voice_name,
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

    @classmethod
    def get_active_handler(cls, session_id: str) -> Optional["ACSMediaHandler"]:
        """Return the active handler for the given Voice Live session id."""
        return _ACTIVE_HANDLERS.get(session_id)

    @classmethod
    def list_active_sessions(cls) -> list[str]:
        """List active Voice Live session ids."""
        return list(_ACTIVE_HANDLERS.keys())

    def __init__(self, config):
        self.endpoint = config["AZURE_VOICE_LIVE_ENDPOINT"]
        self.model = config["VOICE_LIVE_MODEL"]
        self.api_key = config["AZURE_VOICE_LIVE_API_KEY"]
        self._credential = DefaultAzureCredential()
        self.primary_voice = config.get(
            "AZURE_VOICE_PRIMARY_NAME",
            "en-US-Ava:DragonHDLatestNeural",
        )
        self.fallback_voice = config.get("AZURE_VOICE_FALLBACK_NAME")
        self.voice_in_use = self.primary_voice
        self._voice_fallback_used = False
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
        # Track tele-health specific conversation context
        self.patient_context: Optional[str] = None
        self.patient_id: Optional[str] = None
        self.patient_profile: Optional[Dict[str, Any]] = None
        self.default_patient_id: str = config.get("DEFAULT_PATIENT_ID", "PATIENT001")
        self._context_task: Optional[asyncio.Task] = None
        self._sent_auto_greeting: bool = False
        self._pending_background_payload: Optional[Dict[str, Any]] = None
        # Email readback support
        self._last_email_readback: Optional[str] = None
        self._pending_email_readback: Optional[Dict[str, str]] = None

    async def _request_email_readback(self, email: str) -> None:
        """Ask Voice Live to restate an email address using SSML.

        The Realtime/Voice Live API doesn't explicitly document an SSML content part.
        This implementation asks the model to output SSML-only, which some Voice Live
        voice pipelines can interpret as SSML. If the service reports an error event,
        we fall back to a plain spelled-out string.
        """

        if not self._is_ws_connected():
            logger.debug(
                "[VoiceLiveACSHandler] Email readback skipped; websocket not connected"
            )
            return

        normalized = (email or "").strip()
        if not normalized:
            logger.debug("[VoiceLiveACSHandler] Email readback skipped; empty email")
            return

        email_for_log = normalized

        ssml = _email_to_ssml(normalized, break_ms=200)
        fallback = _email_to_spoken_tokens(normalized)

        logger.info(
            "[VoiceLiveACSHandler] Email readback requested for %s (ssml_chars=%d)",
            email_for_log,
            len(ssml),
        )

        # Keep a pending fallback in case the service rejects the SSML.
        self._pending_email_readback = {"email": normalized, "fallback": fallback}

        instructions = (
            "The user just provided an email address. Repeat the email address back exactly. "
            "Do not use phonetic alphabet and do not say 'as in' phrases. "
            "Return ONLY SSML. Use only these tags: speak, say-as, sub, break. "
            "Do not add any other text before or after the SSML. Output exactly this SSML:\n"
            f"{ssml}"
        )

        await self._send_json(
            {
                "type": "response.create",
                "response": {
                    "modalities": ["audio"],
                    "instructions": instructions,
                    "cancel_previous": True,
                },
            }
        )
        self._response_in_progress = True
        logger.debug(
            "[VoiceLiveACSHandler] response.create sent for email readback (%s)",
            email_for_log,
        )

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

        # Build WebSocket URL for realtime endpoint and specify model via query parameter
        base_ws = self.endpoint.rstrip("/").replace("https://", "wss://")
        url = (
            f"{base_ws}/voice-live/realtime?api-version={api_version}"
            f"&model={quote(self.model)}"
        )

        # Auth strategy:
        # - Default: Microsoft Entra ID via DefaultAzureCredential (Bearer token)
        # - Optional: API key (if explicitly enabled)
        use_api_key = (
            bool(self.api_key)
            and os.getenv("AZURE_VOICE_LIVE_USE_API_KEY", "").strip().lower() in {"1", "true", "yes"}
        )

        headers = {"x-ms-client-request-id": self._generate_guid()}

        if use_api_key:
            headers["api-key"] = self.api_key
            logger.info("[VoiceLiveACSHandler] Connecting to Voice Live using API key auth")
        else:
            token = await self._get_voicelive_access_token()
            headers["Authorization"] = f"Bearer {token}"
            logger.info("[VoiceLiveACSHandler] Connecting to Voice Live using DefaultAzureCredential")

        # Establish websocket connection
        self.ws = await ws_connect(url, additional_headers=headers)
        logger.info("[VoiceLiveACSHandler] Connected to Voice Live API")

        await self._initialize_patient_context()

        instructions = self._compose_instructions()
        logger.info(
            "[VoiceLiveACSHandler] Initial system instructions:\n%s",
            instructions,
        )

        await self._send_json(session_config(self.voice_in_use))
        # Don't send response.create immediately - wait for user input

        asyncio.create_task(self._receiver_loop())
        self.send_task = asyncio.create_task(self._sender_loop())

    async def _get_voicelive_access_token(self) -> str:
        """Acquire an access token for Voice Live.

        Voice Live supports Entra tokens minted for the `https://ai.azure.com/.default`
        scope (and legacy `https://cognitiveservices.azure.com/.default`).
        """

        loop = asyncio.get_running_loop()

        def _get_token_sync() -> str:
            last_error: Optional[Exception] = None
            for scope in _VOICE_LIVE_AAD_SCOPES:
                try:
                    return self._credential.get_token(scope).token
                except Exception as exc:  # keep trying scopes
                    last_error = exc
            if isinstance(last_error, ClientAuthenticationError):
                raise last_error
            raise ClientAuthenticationError(message=str(last_error) if last_error else "auth failed")

        return await loop.run_in_executor(None, _get_token_sync)

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

    async def _switch_voice(self, voice_name: str, reason: str):
        """Reconfigure the session to use a different voice."""
        if voice_name == self.voice_in_use:
            return

        logger.warning(
            "[VoiceLiveACSHandler] Switching voice from %s to %s (%s)",
            self.voice_in_use,
            voice_name,
            reason,
        )
        self.voice_in_use = voice_name
        await self._send_json(session_config(self.voice_in_use))

    async def _handle_synthesis_failure(self, context: str):
        """Handle synthesis errors by attempting a voice fallback."""
        if (
            not self._voice_fallback_used
            and self.fallback_voice
            and self.fallback_voice != self.voice_in_use
        ):
            self._voice_fallback_used = True
            await self._switch_voice(self.fallback_voice, context)
            await self._maybe_request_response("retry_after_fallback")
        else:
            logger.error(
                "[VoiceLiveACSHandler] Speech synthesis failed with voice %s after fallback. Context: %s",
                self.voice_in_use,
                context,
            )

    async def inject_tool_result(
        self,
        function_name: str,
        *,
        arguments: Optional[Dict[str, Any]] = None,
        output: Optional[Any] = None,
        silent: bool = True,
        response_modalities: Optional[list[str]] = None,
    ) -> str:
        """Inject a function call + result directly into the conversation.

        This enables background services to push fresh data into the active
        Voice Live session without waiting for a user utterance.
        """

        if not self._is_ws_connected():
            raise RuntimeError("Voice Live websocket is not connected")

        call_id = self._generate_call_id()
        args = arguments or {}

        patient_id = args.get("patient_id")
        if isinstance(patient_id, str):
            normalized_patient_id = patient_id.strip()
            if normalized_patient_id:
                args["patient_id"] = normalized_patient_id
                self._schedule_patient_context_refresh(normalized_patient_id)

        logger.info(
            "[VoiceLiveACSHandler] Injecting tool result: name=%s call_id=%s silent=%s",
            function_name,
            call_id,
            silent,
        )

        # Compose and send the synthetic function call item so the transcript
        # reflects how the data entered the conversation.
        await self._send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": function_name,
                    "arguments": json.dumps(args),
                },
            }
        )

        # Generate the tool output if it was not provided explicitly.
        if output is None:
            output_value: Any = await handle_function_call(function_name, args)
        else:
            output_value = output

        await self._send_function_call_output_item(call_id, output_value)

        response_payload: Optional[Dict[str, Any]] = None
        if response_modalities:
            allowed_modalities = {"text", "audio", "animation", "avatar"}
            invalid_modalities = [m for m in response_modalities if m not in allowed_modalities]
            if invalid_modalities:
                raise ValueError(
                    "Invalid response modalities: %s" % ", ".join(invalid_modalities)
                )
            response_payload = {
                "type": "response.create",
                "response": {"modalities": response_modalities},
            }
        elif not silent:
            response_payload = {"type": "response.create"}

        if response_payload:
            await self._send_json(response_payload)
            self._response_in_progress = True
        return call_id

    def _compose_instructions(self) -> str:
        return compose_instructions()

    async def _send_function_call_output_item(self, call_id: str, output: Any) -> None:
        """Emit a transcript-only function output item without prompting speech."""

        if isinstance(output, (dict, list)):
            payload = json.dumps(output)
        else:
            payload = str(output)

        await self._send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": payload,
                },
            }
        )

    async def _initialize_patient_context(self) -> None:
        """Bootstrap patient context before the first session update."""

        default_patient_id = (self.default_patient_id or "PATIENT001").strip()
        if not default_patient_id:
            return

        self.patient_id = default_patient_id
        await self._apply_patient_context(default_patient_id)

    def _schedule_patient_context_refresh(self, patient_id: str) -> None:
        if patient_id == self.patient_id and self.patient_profile:
            return

        self.patient_id = patient_id

        if self._context_task and not self._context_task.done():
            self._context_task.cancel()

        self._context_task = asyncio.create_task(self._refresh_patient_context(patient_id))
        self._context_task.add_done_callback(self._on_context_task_done)

    async def _refresh_patient_context(self, patient_id: str) -> None:
        try:
            await self._apply_patient_context(patient_id)
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            raise
        except Exception:  # pragma: no cover - defensive logging
            logger.exception(
                "[VoiceLiveACSHandler] Failed to refresh patient context for %s",
                patient_id,
            )
        finally:
            self._context_task = None

    async def _apply_patient_context(self, patient_id: str) -> None:
        loop = asyncio.get_running_loop()

        context_text: Optional[str] = None
        try:
            context_text = await loop.run_in_executor(
                None, build_patient_context, patient_id
            )
        except Exception:
            logger.exception(
                "[VoiceLiveACSHandler] Failed to build patient overview for %s",
                patient_id,
            )
            context_text = None

        profile = get_patient_profile(patient_id)

        self.patient_context = context_text
        self.patient_profile = profile

        if not self.ws or not self._is_ws_connected():
            return

        background_payload: Dict[str, Any] = {"patientId": patient_id}
        if profile:
            background_payload["profile"] = profile
        if context_text:
            background_payload["overview"] = context_text

        if len(background_payload) > 1:
            self._pending_background_payload = background_payload
            if self.session_id and self._is_ws_connected():
                await self._inject_background_context(background_payload)
        else:
            self._pending_background_payload = None

    async def _inject_background_context(self, payload: Dict[str, Any]) -> None:
        message = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"BACKGROUND_PATIENT {json.dumps(payload, ensure_ascii=False)}",
                    }
                ],
            },
        }

        await self._send_json(message)
        logger.info(
            "[VoiceLiveACSHandler] Injected patient background context:\n%s",
            json.dumps(payload, indent=2, ensure_ascii=False),
        )
        self._pending_background_payload = None

    def _on_context_task_done(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover - defensive logging
            logger.exception(
                "[VoiceLiveACSHandler] Patient context update task raised an error"
            )

    def _is_ws_connected(self) -> bool:
        """Best-effort check for an open Voice Live websocket across library versions."""
        if not self.ws:
            return False

        closed_attr = getattr(self.ws, "closed", None)
        if isinstance(closed_attr, bool):
            if closed_attr:
                return False
        elif hasattr(closed_attr, "done"):
            try:
                if closed_attr.done():
                    return False
            except Exception:
                pass

        open_attr = getattr(self.ws, "open", None)
        if isinstance(open_attr, bool):
            return open_attr

        state = getattr(self.ws, "state", None)
        if state is not None:
            state_name = getattr(state, "name", None)
            if state_name:
                return state_name.upper() == "OPEN"
            state_str = str(state).upper()
            if "OPEN" in state_str:
                return True
            return False

        return True

    def _generate_call_id(self) -> str:
        """Create a Voice Live compliant call id (<=32 chars)."""
        return uuid.uuid4().hex

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
                            self._voice_fallback_used = False
                            self.voice_in_use = self.primary_voice
                            if session_id:
                                _register_handler(session_id, self)
                            if not self._sent_auto_greeting:
                                await self._maybe_request_response("session_created_greeting")
                                self._sent_auto_greeting = True
                            if self._pending_background_payload and self._is_ws_connected():
                                await self._inject_background_context(self._pending_background_payload)

                        case "input_audio_buffer.cleared":
                            logger.debug("Input audio buffer cleared")

                        case "input_audio_buffer.speech_started":
                            logger.debug(
                                "Voice activity detection started at %s ms",
                                event.get("audio_start_ms"),
                            )
                            await self.stop_audio()

                        case "input_audio_buffer.speech_stopped":
                            logger.debug("Speech stopped")
                            # Trigger response generation after user stops speaking
                            # Azure Semantic VAD commits automatically when speech ends.
                            await self._maybe_request_response("speech_stopped")

                        case "conversation.item.input_audio_transcription.completed":
                            transcript = event.get("transcript")
                            logger.debug("User transcript: %s", transcript)
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

                            # If the user provided an email address, trigger a dedicated readback.
                            if isinstance(transcript, str):
                                match = _EMAIL_REGEX.search(transcript)
                                if match:
                                    email = match.group(0)
                                    email_for_log = email
                                    logger.info(
                                        "[VoiceLiveACSHandler] Detected email in transcript: %s",
                                        email_for_log,
                                    )
                                    if email != self._last_email_readback:
                                        self._last_email_readback = email
                                        try:
                                            await self._request_email_readback(email)
                                        except Exception:
                                            logger.exception(
                                                "[VoiceLiveACSHandler] Failed to request email readback"
                                            )
                                    else:
                                        logger.debug(
                                            "[VoiceLiveACSHandler] Skipping email readback (duplicate): %s",
                                            email_for_log,
                                        )

                        case "conversation.item.input_audio_transcription.failed":
                            error_msg = event.get("error")
                            logger.warning("Transcription Error: %s", error_msg)

                        case "response.created":
                            response_id = event.get("response", {}).get("id")
                            logger.debug("Response created: %s", response_id)
                            self._response_in_progress = True

                        case "response.done":
                            response = event.get("response", {})
                            logger.debug("Response done: Id=%s", response.get("id"))
                            self._response_in_progress = False
                            if self._pending_email_readback:
                                pending_email = self._pending_email_readback.get("email")
                                email_for_log = pending_email
                                logger.debug(
                                    "[VoiceLiveACSHandler] Clearing pending email readback after response.done (%s)",
                                    email_for_log,
                                )
                                self._pending_email_readback = None
                            status_details = response.get("status_details")
                            if status_details:
                                logger.info(
                                    "Status Details: %s",
                                    json.dumps(status_details, indent=2),
                                )
                                error_code = (
                                    status_details.get("error", {}).get("code")
                                    if isinstance(status_details, dict)
                                    else None
                                )
                                if error_code == "speech_synthesis_error":
                                    await self._handle_synthesis_failure("status_details")

                        case "response.failed":
                            response = event.get("response", {})
                            logger.warning("Response failed: %s", response)
                            self._response_in_progress = False
                            error_code = response.get("error", {}).get("code") if isinstance(response, dict) else None
                            if self._pending_email_readback:
                                pending = self._pending_email_readback
                                self._pending_email_readback = None
                                fallback = pending.get("fallback")
                                pending_email = pending.get("email")
                                email_for_log = pending_email
                                logger.warning(
                                    "[VoiceLiveACSHandler] Email readback response.failed; falling back (%s)",
                                    email_for_log,
                                )
                                if fallback:
                                    try:
                                        await self._send_json(
                                            {
                                                "type": "response.create",
                                                "response": {
                                                    "modalities": ["audio"],
                                                    "instructions": (
                                                        "Repeat the user's email address back, spelling it out character by character. "
                                                        "Say 'at' for @, 'dot' for ., 'underscore' for _, and 'hyphen' for -. "
                                                        f"Say exactly: {fallback}"
                                                    ),
                                                    "cancel_previous": True,
                                                },
                                            }
                                        )
                                        self._response_in_progress = True
                                    except Exception:
                                        logger.exception(
                                            "[VoiceLiveACSHandler] Failed to send fallback email readback"
                                        )
                            if error_code == "speech_synthesis_error":
                                await self._handle_synthesis_failure("response_failed_event")

                        case "response.interrupted":
                            logger.info("Response interrupted by service")
                            self._response_in_progress = False
                            if self._pending_email_readback:
                                pending_email = self._pending_email_readback.get("email")
                                email_for_log = pending_email
                                logger.debug(
                                    "[VoiceLiveACSHandler] Clearing pending email readback after response.interrupted (%s)",
                                    email_for_log,
                                )
                                self._pending_email_readback = None

                        case "response.canceled" | "response.cancelled":
                            logger.warning("Response canceled: %s", event.get("response"))
                            self._response_in_progress = False
                            if self._pending_email_readback:
                                pending_email = self._pending_email_readback.get("email")
                                email_for_log = pending_email
                                logger.debug(
                                    "[VoiceLiveACSHandler] Clearing pending email readback after response.canceled (%s)",
                                    email_for_log,
                                )
                                self._pending_email_readback = None

                        case "response.text.delta":
                            text = event.get("text", "")
                            logger.debug("Response text delta: %s", text)

                        case "response.text.done":
                            text = event.get("text", "")
                            logger.info("Assistant: %s", text)
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
                            logger.debug("Output item added: %s", item_type)
                            if item_type == "function_call":
                                function_name = item.get("name")
                                logger.debug("Function call item added: %s", function_name)

                        case "response.output_item.done":
                            item = event.get("item", {})
                            item_type = item.get("type")
                            if item_type == "function_call":
                                call_id = item.get("call_id")
                                function_name = item.get("name")
                                arguments = item.get("arguments")
                                logger.debug("Function call item done: %s (call_id: %s) with args: %s", function_name, call_id, arguments)
                                
                                # Handle the function call
                                try:
                                    args_dict = json.loads(arguments) if arguments else {}
                                    result = await handle_function_call(function_name, args_dict)
                                    await self._send_function_call_output_item(call_id, result)

                                    # Trigger response generation (speech comes from response.create, not the item itself)
                                    await self._maybe_request_response("function_call_complete")
                                    
                                except Exception as e:
                                    logger.exception("Error handling function call: %s", e)
                                    await self._send_function_call_output_item(
                                        call_id,
                                        f"Error processing request: {str(e)}",
                                    )
                                    await self._maybe_request_response("function_call_error")

                        case "response.function_call_arguments.delta":
                            arguments = event.get("delta", "")
                            logger.debug("Function call arguments delta: %s", arguments)

                        case "response.function_call_arguments.done":
                            item_id = event.get("item_id")
                            name = event.get("name")
                            arguments = event.get("arguments")
                            call_id = event.get("call_id")
                            logger.debug("Function call arguments done: %s (call_id: %s) with args: %s", name, call_id, arguments)
                            
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
                                    logger.debug(
                                        "[VoiceLiveACSHandler] Audio delta event: %s",
                                        json.dumps(event),
                                    )
                                    await self.voicelive_to_acs(base64_audio)
                            except Exception:
                                logger.exception("Error handling audio delta")

                        case "error":
                            error_info = event.get("error")

                            code: Optional[str] = None
                            if isinstance(error_info, dict):
                                code = error_info.get("code")

                            if code == "conversation_already_has_active_response":
                                logger.debug(
                                    "[VoiceLiveACSHandler] Response still active; deferring new response.create"
                                )
                                # Keep _response_in_progress latched until we see response.done/failed
                            else:
                                logger.error("Voice Live Error: %s", error_info)
                                self._response_in_progress = False

                                # If we were trying to read back an email using SSML, fall back to a
                                # plain spelled-out string.
                                if self._pending_email_readback:
                                    pending = self._pending_email_readback
                                    fallback = pending.get("fallback")
                                    pending_email = pending.get("email")
                                    email_for_log = pending_email
                                    logger.warning(
                                        "[VoiceLiveACSHandler] Email readback failed; using fallback (%s)",
                                        email_for_log,
                                    )
                                    self._pending_email_readback = None
                                    if fallback:
                                        try:
                                            await self._send_json(
                                                {
                                                    "type": "response.create",
                                                    "response": {
                                                        "modalities": ["audio"],
                                                        "instructions": (
                                                            "Repeat the user's email address back, spelling it out character by character. "
                                                            "Say 'at' for @, 'dot' for ., 'underscore' for _, and 'hyphen' for -. "
                                                            f"Say exactly: {fallback}"
                                                        ),
                                                        "cancel_previous": True,
                                                    },
                                                }
                                            )
                                            self._response_in_progress = True
                                            logger.debug(
                                                "[VoiceLiveACSHandler] Fallback email readback response.create sent (%s)",
                                                email_for_log,
                                            )
                                        except Exception:
                                            logger.exception(
                                                "[VoiceLiveACSHandler] Failed to send fallback email readback"
                                            )
                                if code == "speech_synthesis_error":
                                    await self._handle_synthesis_failure("error_event")

                        case _:
                            logger.debug("Unhandled event type: %s", event_type)
                except json.JSONDecodeError:
                    logger.debug("Received non-json message")
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Receiver loop error")
        finally:
            _unregister_handler(self.session_id)
            self.ws = None

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
