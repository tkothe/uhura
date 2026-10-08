"""Thin client for the ElevenLabs Agents API.

Verified against a real account: text-only conversations including per-call overrides
and webhook tools, and phone calls through a Twilio number (see docs/verified.md). The
SIP-trunk endpoint follows the published API schema but has not placed a call yet.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx
import websockets

from .config import Settings

BASE_URL = "https://api.elevenlabs.io"
OUTBOUND_PATHS = {
    "twilio": "/v1/convai/twilio/outbound-call",
    "sip": "/v1/convai/sip-trunk/outbound-call",
}


class TextSession(Protocol):
    """A text-only conversation with the agent, used for rehearsals."""

    async def send(self, text: str) -> None:
        """Say something to the agent as the person being called."""

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        """Yield {"type": "agent_said", "text"} and {"type": "tool_used", "name", "ok"} until the agent hangs up."""

    async def close(self) -> None: ...


class VoiceClient(Protocol):
    async def start_call(
        self, *, to: str, prompt: str, first_message: str, language: str, call_id: str, voicemail: str = ""
    ) -> str:
        """Dial `to` and return the provider's conversation id."""

    async def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Return {"status", "transcript": [{"role", "message"}], "duration_secs", "error", "cost"}."""

    async def delete_conversation(self, conversation_id: str) -> None:
        """Delete the provider's copy of a conversation, transcript included."""

    async def open_text_session(
        self, *, prompt: str, first_message: str, language: str, call_id: str, voicemail: str = ""
    ) -> TextSession:
        """Start a text-only conversation with the same briefing a real call would get."""


def initiation_data(
    *, prompt: str, first_message: str, language: str, call_id: str, voicemail: str = ""
) -> dict[str, Any]:
    """What makes the generic agent this call's agent: briefing, opening line, language."""
    return {
        # The agent's tools send the call id back so the service knows which call is asking;
        # the voicemail-detection tool speaks uhura_voicemail if a mailbox answers.
        "dynamic_variables": {"uhura_call_id": call_id, "uhura_voicemail": voicemail},
        "conversation_config_override": {
            "agent": {"prompt": {"prompt": prompt}, "first_message": first_message, "language": language}
        },
    }


def _error_text(metadata: dict[str, Any]) -> str | None:
    """Why a conversation failed, as text. `error` can be an object, e.g. when the credits ran
    out mid-call: {"code": 1002, "reason": "This request exceeds your quota limit.", …}."""
    error = metadata.get("error")
    if isinstance(error, dict):
        reason = error.get("reason") or error.get("message") or json.dumps(error)
        error = f"{reason} (code {error['code']})" if error.get("code") is not None else reason
    return str(error) if error else metadata.get("termination_reason")


def _cost(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """What ElevenLabs charged for a finished conversation, or None while it does not say.

    `credits` is what was deducted; `billed_minutes` is the call time after the silence
    discount; the dollar figures are ElevenLabs' own prices for the voice platform and
    the language model (the latter is not always deducted as credits)."""
    if metadata.get("cost") is None:
        return None
    charging = metadata.get("charging") or {}
    voice = ((charging.get("platform_usage") or {}).get("category_usage") or {}).get("voice") or {}
    minutes = voice.get("quantity")
    return {
        "credits": metadata["cost"],
        "billed_minutes": round(minutes, 2) if minutes is not None else None,
        "platform_usd": charging.get("platform_price"),
        "llm_usd": charging.get("llm_price"),
    }


class ElevenLabsTextSession:
    def __init__(self, ws: websockets.ClientConnection):
        self._ws = ws

    async def send(self, text: str) -> None:
        await self._ws.send(json.dumps({"type": "user_message", "text": text}))

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        try:
            async for raw in self._ws:
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "ping":
                    await self._ws.send(json.dumps({"type": "pong", "event_id": message["ping_event"]["event_id"]}))
                elif kind == "agent_response":
                    text = message["agent_response_event"]["agent_response"].strip()
                    if text:
                        yield {"type": "agent_said", "text": text}
                elif kind == "agent_tool_response":
                    tool = message["agent_tool_response"]
                    yield {"type": "tool_used", "name": tool.get("tool_name"), "ok": not tool.get("is_error")}
        except websockets.ConnectionClosed:
            pass

    async def close(self) -> None:
        await self._ws.close()


class ElevenLabsClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"xi-api-key": settings.elevenlabs_api_key},
            timeout=30,
            transport=transport,
        )

    async def start_call(
        self, *, to: str, prompt: str, first_message: str, language: str, call_id: str, voicemail: str = ""
    ) -> str:
        try:
            path = OUTBOUND_PATHS[self._settings.telephony]
        except KeyError:
            raise RuntimeError(
                f"UHURA_TELEPHONY must be one of {', '.join(OUTBOUND_PATHS)}, not '{self._settings.telephony}'"
            ) from None
        if not self._settings.elevenlabs_phone_number_id:
            raise RuntimeError("no phone number configured (ELEVENLABS_PHONE_NUMBER_ID is empty)")
        payload = {
            "agent_id": self._settings.elevenlabs_agent_id,
            "agent_phone_number_id": self._settings.elevenlabs_phone_number_id,
            "to_number": to,
            "conversation_initiation_client_data": initiation_data(
                prompt=prompt, first_message=first_message, language=language, call_id=call_id, voicemail=voicemail
            ),
        }
        if self._settings.telephony == "twilio":
            payload["call_recording_enabled"] = False
        resp = await self._http.post(path, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"ElevenLabs refused the call ({resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        if not body.get("success") or not body.get("conversation_id"):
            raise RuntimeError(f"call was not started: {body.get('message', 'no conversation id returned')}")
        return body["conversation_id"]

    async def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"/v1/convai/conversations/{conversation_id}")
        resp.raise_for_status()
        body = resp.json()
        metadata = body.get("metadata") or {}
        return {
            "status": body.get("status"),
            "transcript": [
                {"role": t.get("role"), "message": t.get("message")}
                for t in body.get("transcript") or []
                if t.get("message")
            ],
            "duration_secs": metadata.get("call_duration_secs"),
            "error": _error_text(metadata),
            "cost": _cost(metadata),
        }

    async def delete_conversation(self, conversation_id: str) -> None:
        resp = await self._http.delete(f"/v1/convai/conversations/{conversation_id}")
        resp.raise_for_status()

    async def open_text_session(
        self, *, prompt: str, first_message: str, language: str, call_id: str, voicemail: str = ""
    ) -> TextSession:
        resp = await self._http.get(
            "/v1/convai/conversation/get-signed-url", params={"agent_id": self._settings.elevenlabs_agent_id}
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"ElevenLabs refused the rehearsal ({resp.status_code}): {resp.text[:300]}")
        ws = await websockets.connect(resp.json()["signed_url"])
        data = initiation_data(
            prompt=prompt, first_message=first_message, language=language, call_id=call_id, voicemail=voicemail
        )
        data["conversation_config_override"]["conversation"] = {"text_only": True}
        await ws.send(json.dumps({"type": "conversation_initiation_client_data", **data}))
        return ElevenLabsTextSession(ws)
