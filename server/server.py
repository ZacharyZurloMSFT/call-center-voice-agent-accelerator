import asyncio
import json
import logging
import os

from app.handler.acs_media_handler import ACSMediaHandler
from dotenv import load_dotenv
from quart import Quart, websocket, request, jsonify

load_dotenv()

app = Quart(__name__)
app.config["AZURE_VOICE_LIVE_API_KEY"] = os.getenv("AZURE_VOICE_LIVE_API_KEY", "")
app.config["AZURE_VOICE_LIVE_ENDPOINT"] = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
app.config["VOICE_LIVE_MODEL"] = os.getenv("VOICE_LIVE_MODEL", "gpt-4o-mini")
app.config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"] = os.getenv(
    "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", ""
)

# Cosmos DB configuration for conversation storage
app.config["COSMOS_DB_ENDPOINT"] = os.getenv("COSMOS_DB_ENDPOINT", "")
app.config["COSMOS_DB_KEY"] = os.getenv("COSMOS_DB_KEY", "")
app.config["COSMOS_DB_DATABASE_NAME"] = os.getenv("COSMOS_DB_DATABASE_NAME", "conversationdb")
app.config["COSMOS_DB_CONTAINER_NAME"] = os.getenv("COSMOS_DB_CONTAINER_NAME", "transcripts")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s"
)


@app.websocket("/web/ws")
async def web_ws():
    """WebSocket endpoint for web clients to send audio to Voice Live."""
    logger = logging.getLogger("web_ws")
    logger.info("Incoming Web WebSocket connection")
    handler = ACSMediaHandler(app.config)
    await handler.init_incoming_websocket(websocket, is_raw_audio=True)
    asyncio.create_task(handler.connect())
    try:
        while True:
            msg = await websocket.receive()
            # if msg is binary audio data, forward to Voice Live
            if isinstance(msg, (bytes, bytearray)):
                await handler.web_to_voicelive(msg)
            else:
                # try to parse JSON commands from the web client
                try:
                    payload = json.loads(msg)
                    if isinstance(payload, dict) and payload.get("Kind") == "UploadTranscript":
                        logger.info("web_ws received UploadTranscript command from client")
                        try:
                            uploaded = await handler.upload_transcript()
                            if uploaded:
                                logger.info("Transcript upload successful for session %s", getattr(handler, 'session_id', 'unknown'))
                            else:
                                logger.info("Transcript upload was skipped or failed for session %s", getattr(handler, 'session_id', 'unknown'))
                        except Exception:
                            logger.exception("Error uploading transcript for session %s", getattr(handler, 'session_id', 'unknown'))
                        continue
                except Exception:
                    # not a JSON command, ignore
                    logger.debug("web_ws received non-json text message; ignoring")
    except Exception:
        logger.exception("Web WebSocket connection closed")


@app.route("/")
async def index():
    """Serves the static index page."""
    return await app.send_static_file("index.html")


@app.post("/api/tools/run")
async def api_run_tool():
    """Trigger a Voice Live tool/function for an active session."""
    payload = await request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body required"}), 400

    session_id = payload.get("session_id")
    function_name = payload.get("function_name")
    arguments = payload.get("arguments") or {}
    output = payload.get("output")
    silent = bool(payload.get("silent", True))
    response_modalities = payload.get("response_modalities")

    if not session_id or not function_name:
        return jsonify({"error": "session_id and function_name are required"}), 400

    handler = ACSMediaHandler.get_active_handler(session_id)
    if not handler:
        return jsonify({"error": f"Session {session_id} is not active"}), 404

    if response_modalities is not None and not isinstance(response_modalities, list):
        return jsonify({"error": "response_modalities must be a list when provided"}), 400

    if not isinstance(arguments, dict):
        return jsonify({"error": "arguments must be an object"}), 400

    try:
        call_id = await handler.inject_tool_result(
            function_name,
            arguments=arguments,
            output=output,
            silent=silent,
            response_modalities=response_modalities,
        )
    except Exception as exc:
        logging.getLogger("api.tools").exception("Failed to trigger tool")
        return jsonify({"error": str(exc)}), 500

    return jsonify({"call_id": call_id})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)
