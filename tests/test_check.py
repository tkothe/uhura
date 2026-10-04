import httpx

from uhura.check import check_account
from uhura.config import Settings

AGENT = {
    "conversation_config": {
        "agent": {
            "prompt": {
                "built_in_tools": {
                    "end_call": {"name": "end_call"},
                    "play_keypad_touch_tone": {"name": "play_keypad_touch_tone"},
                    "skip_turn": {"name": "skip_turn"},
                    "language_detection": None,
                },
                "tool_ids": ["t1", "t2", "t3"],
            }
        }
    },
    "platform_settings": {
        "privacy": {"record_voice": False},
        "auth": {"enable_auth": True},
        "overrides": {
            "conversation_config_override": {
                "agent": {"prompt": {"prompt": True}, "first_message": True, "language": True}
            }
        },
    },
}


def tool(name):
    return {"tool_config": {"name": name, "api_schema": {"url": f"https://uhura.invalid/agent-tools/{name}"}}}


def run(agent=AGENT, **overrides):
    def handler(request):
        path = request.url.path
        if path == "/v1/user/subscription":
            return httpx.Response(200, json={"tier": "free"})
        if path.startswith("/v1/convai/agents/"):
            return httpx.Response(200, json=agent)
        if path.startswith("/v1/convai/tools/"):
            return httpx.Response(
                200,
                json=tool(
                    {"t1": "ask_principal", "t2": "final_check", "t3": "report_progress"}[path.rsplit("/", 1)[1]]
                ),
            )
        return httpx.Response(404)

    values = dict(elevenlabs_api_key="k", elevenlabs_agent_id="agent_1")
    values.update(overrides)
    return {name: ok for name, ok, _ in check_account(Settings(**values), transport=httpx.MockTransport(handler))}


def test_a_complete_agent_passes_all_account_checks_except_what_is_missing():
    results = run()
    # The tools' address is unreachable and no number is configured; everything else is fine.
    failing = {name for name, ok in results.items() if not ok}
    assert failing == {"tools can reach this service", "phone number configured"}
    assert results["no audio is stored"] and results["agent requires authentication"]
    assert results["tool ask_principal attached"] and results["tool final_check attached"]


def test_unsafe_agent_settings_are_flagged():
    agent = {
        **AGENT,
        "platform_settings": {"privacy": {"record_voice": True}, "auth": {"enable_auth": False}, "overrides": {}},
    }
    results = run(agent)
    assert results["no audio is stored"] is False
    assert results["agent requires authentication"] is False
    assert results["per-call briefing, opening line and language allowed"] is False


def test_an_agent_without_keypad_or_skip_turn_is_flagged():
    prompt = AGENT["conversation_config"]["agent"]["prompt"]
    agent = {
        **AGENT,
        "conversation_config": {"agent": {"prompt": {**prompt, "built_in_tools": {"end_call": {}, "skip_turn": None}}}},
    }
    assert run(agent)["agent can press keys in phone menus and wait silently on hold"] is False


def test_a_bad_key_stops_the_checks_early():
    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    results = list(check_account(Settings(elevenlabs_api_key="k"), transport=transport))
    assert results == [("ElevenLabs API key works", False, "HTTP 401")]


def test_missing_agent_id_is_reported():
    assert run(elevenlabs_agent_id="")["agent configured"] is False
