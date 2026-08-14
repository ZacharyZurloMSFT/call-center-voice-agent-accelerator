"""Probe Azure Voice Live agent routing parameters.

This script is intended for local debugging to discover which query keys
(api-version + agent/project routing) are accepted by the Voice Live gateway.

Usage (from server/ venv):
  python voicelive_probe.py

It reads:
  AZURE_VOICE_LIVE_ENDPOINT
  AZURE_VOICE_LIVE_API_KEY (optional)
  AZURE_VOICE_LIVE_USE_API_KEY (optional)
  VOICE_LIVE_MODEL (optional)
  FOUNDRY_PROJECT_ID
  FOUNDRY_GROUP_CHAT_AGENT_ID
  FOUNDRY_AGENT_CONNECTION_STRING (optional)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Mapping, Optional

from azure.ai.voicelive.aio import connect
from azure.ai.voicelive.aio._patch import ConnectionClosed
from azure.ai.voicelive.models import (
    AzureSemanticVad,
    AzureStandardVoice,
    InputAudioFormat,
    OutputAudioFormat,
    RequestSession,
    ServerEventType,
)
from azure.core.credentials import AzureKeyCredential
from azure.identity.aio import DefaultAzureCredential


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _make_credential():
    api_key = _env("AZURE_VOICE_LIVE_API_KEY")
    use_api_key = api_key and _env("AZURE_VOICE_LIVE_USE_API_KEY").lower() in {"1", "true", "yes"}
    if use_api_key:
        return AzureKeyCredential(api_key), "api-key"
    return DefaultAzureCredential(), "aad"


def _base_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if endpoint.startswith("wss://"):
        endpoint = "https://" + endpoint[len("wss://") :]
    return endpoint


def _session_update_payload() -> RequestSession:
    sampling_rate = int(_env("VOICE_LIVE_INPUT_SAMPLING_RATE", "24000"))
    voice_name = _env("VOICE_LIVE_VOICE", "en-US-Ava:DragonHDLatestNeural")

    return RequestSession(
        modalities=["audio"],
        voice=AzureStandardVoice(
            type="azure-standard",
            name=voice_name,
            temperature=0.8,
        ),
        turn_detection=AzureSemanticVad(
            type="azure_semantic_vad",
            threshold=0.5,
            prefix_padding_ms=300,
            silence_duration_ms=800,
            remove_filler_words=False,
        ),
        input_audio_sampling_rate=sampling_rate,
        input_audio_format=InputAudioFormat.PCM16,
        output_audio_format=OutputAudioFormat.PCM16,
        # Keep transcription off for probing.
        input_audio_transcription=None,
        # Keep instructions/tools off for probing.
        instructions=None,
        tools=None,
    )


async def _attempt(
    *,
    name: str,
    endpoint: str,
    credential: Any,
    api_version: str,
    model: Optional[str],
    query: Optional[Mapping[str, Any]],
) -> None:
    print("\n===", name, "===")
    print("api_version=", api_version)
    print("model=", model)
    print("query=", dict(query or {}))

    try:
        async with connect(
            endpoint=endpoint,
            credential=credential,
            api_version=api_version,
            model=model,
            query=query,
            headers={"x-ms-client-request-id": "voicelive-probe"},
        ) as conn:
            ws = getattr(conn, "_connection", None)
            print("connected; ws.closed=", getattr(ws, "closed", None), "close_code=", getattr(ws, "close_code", None))

            # Try to create session
            await conn.session.update(session=_session_update_payload())
            print("sent session.update")

            # Wait for first outcome event
            outcome = None
            async def _wait_events():
                nonlocal outcome
                async for evt in conn:
                    if evt.type in {ServerEventType.SESSION_CREATED, "session.created"}:
                        outcome = ("session.created", getattr(getattr(evt, "session", None), "id", None))
                        return
                    if evt.type in {ServerEventType.ERROR, "error"}:
                        outcome = ("error", getattr(evt, "error", None))
                        return

            try:
                await asyncio.wait_for(_wait_events(), timeout=6.0)
            except asyncio.TimeoutError:
                outcome = ("timeout", None)

            print("outcome=", outcome)
    except ConnectionClosed as e:
        print("ConnectionClosed:", getattr(e, "code", None), str(e))
    except Exception as e:
        print(type(e).__name__ + ":", str(e))


async def main() -> None:
    endpoint = _env("AZURE_VOICE_LIVE_ENDPOINT")
    if not endpoint:
        raise SystemExit("AZURE_VOICE_LIVE_ENDPOINT is required")

    endpoint = _base_endpoint(endpoint)
    project_id = _env("FOUNDRY_PROJECT_ID")
    agent_id = _env("FOUNDRY_GROUP_CHAT_AGENT_ID")
    agent_cs = _env("FOUNDRY_AGENT_CONNECTION_STRING")

    if not project_id or not agent_id:
        raise SystemExit("FOUNDRY_PROJECT_ID and FOUNDRY_GROUP_CHAT_AGENT_ID are required")

    credential, auth = _make_credential()
    print("auth=", auth)

    default_model = _env("VOICE_LIVE_MODEL", "gpt-realtime-1.5")

    # Candidate parameter sets.
    attempts = [
        # Agent scenario (SDK doc: model may be omitted)
        {
            "name": "agent_minimal_stable_projectName_agentId_no_model",
            "api_version": "2025-10-01",
            "model": None,
            "query": {"projectName": project_id, "agentId": agent_id},
        },
        {
            "name": "agent_minimal_preview_projectName_agentId_no_model",
            "api_version": "2025-05-01-preview",
            "model": None,
            "query": {"projectName": project_id, "agentId": agent_id},
        },
        # Common snake_case variant
        {
            "name": "agent_snakecase_stable_project_id_agent_id_no_model",
            "api_version": "2025-10-01",
            "model": None,
            "query": {"project_id": project_id, "agent_id": agent_id},
        },
        # With model (in case this gateway still requires it)
        {
            "name": "agent_minimal_stable_projectName_agentId_with_model",
            "api_version": "2025-10-01",
            "model": default_model,
            "query": {"projectName": project_id, "agentId": agent_id},
        },
    ]

    if agent_cs:
        attempts.insert(
            1,
            {
                "name": "agent_minimal_stable_with_agentConnectionString",
                "api_version": "2025-10-01",
                "model": None,
                "query": {"projectName": project_id, "agentId": agent_id, "agentConnectionString": agent_cs},
            },
        )

    try:
        for a in attempts:
            await _attempt(
                name=a["name"],
                endpoint=endpoint,
                credential=credential,
                api_version=a["api_version"],
                model=a["model"],
                query=a["query"],
            )
    finally:
        if auth == "aad":
            await credential.close()


if __name__ == "__main__":
    asyncio.run(main())
