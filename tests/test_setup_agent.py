import json

import httpx
import pytest

from uhura.config import Settings
from uhura.setup_agent import SECRET_NAME, SetupError, agent_config, setup_agent, setup_tools, tool_configs


class FakeElevenLabs:
    """Records requests and answers like the parts of the ElevenLabs API the setup uses."""

    def __init__(self, tools=(), secrets=()):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.tools = list(tools)
        self.secrets = list(secrets)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        method, path = request.method, request.url.path
        self.requests.append((method, path, body))
        if (method, path) == ("POST", "/v1/convai/agents/create"):
            return httpx.Response(200, json={"agent_id": "agent_new"})
        if path.startswith("/v1/convai/agents/"):
            return httpx.Response(200, json={"agent_id": path.rsplit("/", 1)[1]})
        if (method, path) == ("GET", "/v1/convai/secrets"):
            return httpx.Response(200, json={"secrets": self.secrets})
        if (method, path) == ("POST", "/v1/convai/secrets"):
            return httpx.Response(200, json={"secret_id": "sec_new", "name": body["name"]})
        if path.startswith("/v1/convai/secrets/"):
            return httpx.Response(200, json={})
        if (method, path) == ("GET", "/v1/convai/tools"):
            return httpx.Response(200, json={"tools": self.tools})
        if (method, path) == ("POST", "/v1/convai/tools"):
            return httpx.Response(200, json={"id": f"tool_{body['tool_config']['name']}"})
        if path.startswith("/v1/convai/tools/"):
            return httpx.Response(200, json={})
        return httpx.Response(404, text="unexpected request")

    def sent(self, method, path):
        return [body for m, p, body in self.requests if (m, p) == (method, path)]


def settings(**overrides):
    values = dict(
        elevenlabs_api_key="k",
        elevenlabs_agent_id="agent_1",
        tool_secret="tool-secret-0123456789abcdef",
        ask_timeout=100,
    )
    values.update(overrides)
    return Settings(**values)


def test_agent_stores_no_audio_requires_auth_and_allows_per_call_overrides():
    platform = agent_config()["platform_settings"]
    assert platform["privacy"]["record_voice"] is False
    assert platform["auth"]["enable_auth"] is True
    allowed = platform["overrides"]["conversation_config_override"]
    assert allowed["agent"] == {"prompt": {"prompt": True}, "first_message": True, "language": True}
    assert allowed["conversation"] == {"text_only": True}


def test_agent_covers_every_disclosure_language_and_uses_the_chosen_model():
    config = agent_config(llm="some-model")["conversation_config"]
    assert config["agent"]["language"] == "de"
    assert list(config["language_presets"]) == ["en"]
    assert "AI assistant" in config["language_presets"]["en"]["overrides"]["agent"]["first_message"]
    assert config["agent"]["prompt"]["llm"] == "some-model"
    assert set(config["agent"]["prompt"]["built_in_tools"]) == {
        "end_call",
        "language_detection",
        "voicemail_detection",
        "play_keypad_touch_tone",
        "skip_turn",
    }
    assert config["conversation"]["max_duration_seconds"] == 1200


def test_menus_and_hold_queues_keep_the_agent_quiet():
    tools = agent_config()["conversation_config"]["agent"]["prompt"]["built_in_tools"]
    assert tools["play_keypad_touch_tone"]["params"]["suppress_turn_after_dtmf"] is True
    # -1: silent until the other side speaks, with no check-in after a timeout.
    assert tools["skip_turn"]["params"]["wait_timeout_secs"] == -1


def test_voice_is_set_only_when_configured():
    assert "voice_id" not in agent_config()["conversation_config"]["tts"]
    assert agent_config(voice_id="voice_1")["conversation_config"]["tts"] == {
        "model_id": "eleven_flash_v2_5",
        "voice_id": "voice_1",
    }


def test_a_language_can_have_its_own_voice():
    presets = agent_config(voice_id="german", language_voices={"en": "english"})["conversation_config"][
        "language_presets"
    ]
    assert presets["en"]["overrides"]["tts"] == {"voice_id": "english"}
    assert "tts" not in agent_config()["conversation_config"]["language_presets"]["en"]["overrides"]


def test_language_voices_are_read_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("UHURA_VOICE_ID", "german")
    monkeypatch.setenv("UHURA_VOICE_ID_EN", "english")
    monkeypatch.setenv("UHURA_VOICE_ID_FR", "")
    loaded = Settings.from_env()
    assert loaded.voice_id == "german" and loaded.language_voices == {"en": "english"}


def test_agent_can_start_conversations_uhura_did_not_place():
    # A dashboard test or an incoming call supplies no call id; the placeholder stands in.
    agent = agent_config()["conversation_config"]["agent"]
    assert agent["dynamic_variables"] == {
        "dynamic_variable_placeholders": {"uhura_call_id": "none", "uhura_voicemail": ""}
    }


def test_voicemail_detection_speaks_the_per_call_message():
    tools = agent_config()["conversation_config"]["agent"]["prompt"]["built_in_tools"]
    assert tools["voicemail_detection"]["params"]["voicemail_message"] == "{{uhura_voicemail}}"
    assert "voicemail_message" not in tools["end_call"]["params"]


def test_setup_agent_creates_when_none_is_configured():
    api = FakeElevenLabs()
    agent = setup_agent(settings(elevenlabs_agent_id=""), transport=httpx.MockTransport(api))
    assert agent["agent_id"] == "agent_new"
    assert api.sent("POST", "/v1/convai/agents/create")[0]["name"] == "Uhura"


def test_setup_agent_updates_the_configured_agent():
    api = FakeElevenLabs()
    agent = setup_agent(settings(llm="some-model"), transport=httpx.MockTransport(api))
    assert agent["agent_id"] == "agent_1"
    assert api.sent("POST", "/v1/convai/agents/create") == []
    patched = api.sent("PATCH", "/v1/convai/agents/agent_1")[0]
    assert patched["conversation_config"]["agent"]["prompt"]["llm"] == "some-model"


def test_setup_needs_an_api_key():
    with pytest.raises(SetupError, match="ELEVENLABS_API_KEY"):
        setup_agent(settings(elevenlabs_api_key=""))


def test_tools_carry_the_call_id_and_reference_the_secret():
    ask, final, progress, refused = tool_configs("https://uhura.example/", "sec_1", ask_timeout=100)
    assert ask["api_schema"]["url"] == "https://uhura.example/agent-tools/ask_principal"
    assert ask["api_schema"]["request_headers"] == {"X-Uhura-Secret": {"secret_id": "sec_1"}}
    properties = ask["api_schema"]["request_body_schema"]["properties"]
    assert properties["call_id"] == {"type": "string", "dynamic_variable": "uhura_call_id"}
    assert "question" in properties
    # ElevenLabs must wait longer than the service does, and never beyond its own 300 s cap.
    assert ask["response_timeout_secs"] == 120
    assert tool_configs("https://x", "s", ask_timeout=900)[0]["response_timeout_secs"] == 300
    assert final["name"] == "final_check" and list(final["api_schema"]["request_body_schema"]["properties"]) == [
        "call_id"
    ]
    # Progress reports must not hold up the conversation or be heard on the line.
    assert progress["name"] == "report_progress"
    assert progress["execution_mode"] == "async" and progress["pre_tool_speech"] == "off"
    assert "tool_call_sound" not in progress
    stage = progress["api_schema"]["request_body_schema"]["properties"]["stage"]
    assert stage["enum"] == ["menu", "hold", "talking", "wrapping_up"]
    # A refusal must reach the service before the call ends, so it is not async.
    assert refused["name"] == "consent_refused" and "execution_mode" not in refused
    assert refused["pre_tool_speech"] == "off"
    assert list(refused["api_schema"]["request_body_schema"]["properties"]) == ["call_id"]


def test_setup_tools_creates_secret_and_tools_and_attaches_them():
    api = FakeElevenLabs()
    ids = setup_tools(settings(), "https://uhura.example", transport=httpx.MockTransport(api))
    assert ids == {
        "ask_principal": "tool_ask_principal",
        "final_check": "tool_final_check",
        "report_progress": "tool_report_progress",
        "consent_refused": "tool_consent_refused",
    }
    assert api.sent("POST", "/v1/convai/secrets") == [
        {"type": "new", "name": SECRET_NAME, "value": "tool-secret-0123456789abcdef"}
    ]
    created = api.sent("POST", "/v1/convai/tools")
    assert all(
        c["tool_config"]["api_schema"]["request_headers"]["X-Uhura-Secret"] == {"secret_id": "sec_new"} for c in created
    )
    # The secret's value appears only in the secret store, never in a tool.
    assert "tool-secret-0123456789abcdef" not in json.dumps(created)
    attached = api.sent("PATCH", "/v1/convai/agents/agent_1")[0]
    assert attached == {"conversation_config": {"agent": {"prompt": {"tool_ids": list(ids.values())}}}}


def test_setup_tools_updates_existing_tools_and_secret_in_place():
    api = FakeElevenLabs(
        tools=[{"id": "tool_old", "tool_config": {"name": "ask_principal"}}],
        secrets=[{"secret_id": "sec_old", "name": SECRET_NAME}],
    )
    ids = setup_tools(settings(), "https://new.example", transport=httpx.MockTransport(api))
    assert ids == {
        "ask_principal": "tool_old",
        "final_check": "tool_final_check",
        "report_progress": "tool_report_progress",
        "consent_refused": "tool_consent_refused",
    }
    assert api.sent("POST", "/v1/convai/secrets") == []
    assert api.sent("PATCH", "/v1/convai/secrets/sec_old") == [
        {"type": "update", "name": SECRET_NAME, "value": "tool-secret-0123456789abcdef"}
    ]
    updated = api.sent("PATCH", "/v1/convai/tools/tool_old")[0]
    assert updated["tool_config"]["api_schema"]["url"].startswith("https://new.example/")
    assert len(api.sent("POST", "/v1/convai/tools")) == 3


@pytest.mark.parametrize(
    "changes, url, message",
    [
        ({}, "http://insecure.example", "https://"),
        ({"elevenlabs_agent_id": ""}, "https://x.example", "setup-agent"),
        ({"tool_secret": ""}, "https://x.example", "UHURA_TOOL_SECRET"),
    ],
)
def test_setup_tools_refuses_incomplete_setups(changes, url, message):
    with pytest.raises(SetupError, match=message):
        setup_tools(settings(**changes), url, transport=httpx.MockTransport(FakeElevenLabs()))


def test_api_errors_name_the_request():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="invalid key"))
    with pytest.raises(SetupError, match=r"GET /v1/convai/secrets \(401\): invalid key"):
        setup_tools(settings(), "https://x.example", transport=transport)


def test_setup_tools_refuses_a_weak_tool_secret():
    with pytest.raises(SetupError, match="tool secret"):
        setup_tools(settings(tool_secret="change-me-too"), "https://uhura.example")
