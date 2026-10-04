import asyncio
import json

import httpx
import pytest

from uhura.config import Settings
from uhura.elevenlabs import ElevenLabsClient, ElevenLabsTextSession

CALL = dict(to="+493023125000", prompt="P", first_message="F", language="de", call_id="abc")


def client_with(handler, **settings):
    values = dict(elevenlabs_api_key="k", elevenlabs_agent_id="agent_1", elevenlabs_phone_number_id="phone_1")
    values.update(settings)
    return ElevenLabsClient(Settings(**values), transport=httpx.MockTransport(handler))


def test_twilio_call_sends_briefing_as_overrides():
    seen = {}

    def handler(request):
        seen["path"], seen["key"] = request.url.path, request.headers["xi-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"success": True, "message": "ok", "conversation_id": "conv_9", "callSid": "CA1"}
        )

    assert asyncio.run(client_with(handler).start_call(**CALL)) == "conv_9"
    assert seen["path"] == "/v1/convai/twilio/outbound-call" and seen["key"] == "k"
    body = seen["body"]
    assert body["agent_id"] == "agent_1" and body["agent_phone_number_id"] == "phone_1"
    assert body["to_number"] == "+493023125000"
    assert body["call_recording_enabled"] is False
    data = body["conversation_initiation_client_data"]
    assert data["dynamic_variables"] == {"uhura_call_id": "abc", "uhura_voicemail": ""}
    assert data["conversation_config_override"]["agent"] == {
        "prompt": {"prompt": "P"},
        "first_message": "F",
        "language": "de",
    }


def test_sip_calls_use_the_sip_endpoint():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        assert "call_recording_enabled" not in json.loads(request.content)
        return httpx.Response(200, json={"success": True, "message": "ok", "conversation_id": "c", "sip_call_id": "s"})

    asyncio.run(client_with(handler, telephony="sip").start_call(**CALL))
    assert paths == ["/v1/convai/sip-trunk/outbound-call"]


@pytest.mark.parametrize(
    "settings, response, message",
    [
        ({"elevenlabs_phone_number_id": ""}, None, "no phone number configured"),
        ({"telephony": "carrier-pigeon"}, None, "UHURA_TELEPHONY"),
        ({}, httpx.Response(422, text="bad number"), "refused the call"),
        ({}, httpx.Response(200, json={"success": False, "message": "busy", "conversation_id": None}), "busy"),
    ],
)
def test_call_failures_are_reported(settings, response, message):
    with pytest.raises(RuntimeError, match=message):
        asyncio.run(client_with(lambda request: response, **settings).start_call(**CALL))


def test_conversation_is_reduced_to_what_uhura_stores():
    def handler(request):
        assert request.url.path == "/v1/convai/conversations/conv_9"
        return httpx.Response(
            200,
            json={
                "status": "done",
                "transcript": [
                    {"role": "agent", "message": "Guten Tag", "time_in_call_secs": 0},
                    {"role": "agent", "message": None, "tool_calls": [{}]},
                    {"role": "user", "message": "Hallo"},
                ],
                "metadata": {"call_duration_secs": 31, "termination_reason": "end_call tool"},
            },
        )

    conv = asyncio.run(client_with(handler).get_conversation("conv_9"))
    assert conv == {
        "status": "done",
        "transcript": [{"role": "agent", "message": "Guten Tag"}, {"role": "user", "message": "Hallo"}],
        "duration_secs": 31,
        "error": "end_call tool",
        "cost": None,
    }


def test_an_error_object_becomes_text():
    # As ElevenLabs reported a call cut off when the credits ran out (2026-10-03).
    error = {"code": 1002, "reason": "This request exceeds your quota limit.", "error_type": "dependency_error"}

    def handler(request):
        metadata = {"call_duration_secs": 467, "termination_reason": error["reason"], "error": error}
        return httpx.Response(200, json={"status": "failed", "transcript": [], "metadata": metadata})

    conv = asyncio.run(client_with(handler).get_conversation("conv_9"))
    assert conv["error"] == "This request exceeds your quota limit. (code 1002)"


def test_cost_is_read_from_the_charging_details():
    # Shape and figures as ElevenLabs reported them for a real 467 s call (2026-10-03).
    metadata = {
        "call_duration_secs": 467,
        "cost": 5021,
        "charging": {
            "llm_price": 0.1500372,
            "platform_price": 0.4998721,
            "platform_usage": {"category_usage": {"voice": {"credits": 5021, "quantity": 7.5310817}}},
        },
    }

    def handler(request):
        return httpx.Response(200, json={"status": "done", "transcript": [], "metadata": metadata})

    conv = asyncio.run(client_with(handler).get_conversation("conv_9"))
    assert conv["cost"] == {"credits": 5021, "billed_minutes": 7.53, "platform_usd": 0.4998721, "llm_usd": 0.1500372}


class FakeSocket:
    def __init__(self, incoming):
        self._incoming = [json.dumps(m) for m in incoming]
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def __aiter__(self):
        for raw in self._incoming:
            yield raw

    async def close(self):
        self.closed = True


def test_text_session_translates_the_websocket_protocol():
    socket = FakeSocket(
        [
            {"type": "conversation_initiation_metadata", "conversation_initiation_metadata_event": {}},
            {"type": "agent_chat_response_part", "text_response_part": {"text": "Gu"}},
            {"type": "agent_response", "agent_response_event": {"agent_response": "Guten Tag "}},
            {"type": "ping", "ping_event": {"event_id": 7}},
            {"type": "agent_response", "agent_response_event": {"agent_response": ""}},
            {"type": "agent_tool_response", "agent_tool_response": {"tool_name": "final_check", "is_error": False}},
        ]
    )
    session = ElevenLabsTextSession(socket)

    async def go():
        await session.send("Hallo")
        return [event async for event in session]

    assert asyncio.run(go()) == [
        {"type": "agent_said", "text": "Guten Tag"},
        {"type": "tool_used", "name": "final_check", "ok": True},
    ]
    assert socket.sent == [{"type": "user_message", "text": "Hallo"}, {"type": "pong", "event_id": 7}]
