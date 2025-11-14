import asyncio
import json
import logging
import os

from app.handler.acs_media_handler import ACSMediaHandler
from dotenv import load_dotenv
from quart import Quart, websocket

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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)
