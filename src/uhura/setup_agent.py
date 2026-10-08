"""One-time setup of the ElevenLabs side: the generic caller agent and its tools.

`uhura setup-agent` creates the agent (or updates the one named in ELEVENLABS_AGENT_ID).
`uhura setup-tools` points the agent's tools at the public address of this service; run it
again whenever that address changes.

The per-call briefing, opening line and language are passed as overrides when a call is
placed; the values stored on the agent are safe fallbacks.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings, WeakSecretError, check_secrets
from .disclosures import DISCLOSURES
from .elevenlabs import BASE_URL
from .models import STAGES

FALLBACK_PROMPT = (
    "You are a phone assistant. No briefing was supplied for this call. "
    "Apologise for the disturbance, say the call was placed by mistake, and end the call."
)
# Waiting queues can take a while; silence on hold is billed at a fraction of the rate.
MAX_CALL_SECONDS = 1200
SECRET_NAME = "uhura_tool_secret"
NO_CALL_ID = "none"
BUILT_IN_TOOLS = ("end_call", "language_detection", "voicemail_detection", "play_keypad_touch_tone", "skip_turn")
BUILT_IN_TOOL_PARAMS = {
    # On voicemail, ElevenLabs speaks this message (filled per call) before hanging up.
    "voicemail_detection": {"voicemail_message": "{{uhura_voicemail}}"},
    # Phone menus: press a key, then say nothing that could be taken as input.
    "play_keypad_touch_tone": {"suppress_turn_after_dtmf": True},
    # On hold: stay silent until the other side speaks, instead of checking in every few seconds.
    "skip_turn": {"wait_timeout_secs": -1},
}


class SetupError(RuntimeError):
    pass


class _Api:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        if not settings.elevenlabs_api_key:
            raise SetupError("ELEVENLABS_API_KEY is not set")
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"xi-api-key": settings.elevenlabs_api_key},
            timeout=30,
            transport=transport,
        )

    def __call__(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise SetupError(f"ElevenLabs refused {method} {path} ({resp.status_code}): {resp.text[:400]}")
        return resp.json()


def agent_config(
    name: str = "Uhura",
    llm: str = "gemini-3.5-flash",
    voice_id: str = "",
    language_voices: dict[str, str] | None = None,
) -> dict[str, Any]:
    default, *others = DISCLOSURES  # the first language in disclosures.py is the agent's default
    # Non-English agents need one of the multilingual low-latency models.
    tts = {"model_id": "eleven_flash_v2_5"}
    if voice_id:
        tts["voice_id"] = voice_id

    def preset(lang: str) -> dict[str, Any]:
        overrides: dict[str, Any] = {"agent": {"first_message": DISCLOSURES[lang].format(principal="Uhura")}}
        if lang in (language_voices or {}):
            overrides["tts"] = {"voice_id": language_voices[lang]}
        return {"overrides": overrides}

    return {
        "name": name,
        "conversation_config": {
            "agent": {
                "language": default,
                "first_message": DISCLOSURES[default].format(principal="Uhura"),
                # The tools need a call id. Conversations Uhura did not start (a test in the
                # ElevenLabs dashboard, an incoming call) get this placeholder; without it
                # ElevenLabs refuses to start them at all.
                "dynamic_variables": {
                    "dynamic_variable_placeholders": {"uhura_call_id": NO_CALL_ID, "uhura_voicemail": ""}
                },
                "prompt": {
                    "prompt": FALLBACK_PROMPT,
                    "llm": llm,
                    "built_in_tools": {
                        tool: {"name": tool, "params": {"system_tool_type": tool, **BUILT_IN_TOOL_PARAMS.get(tool, {})}}
                        for tool in BUILT_IN_TOOLS
                    },
                },
            },
            "language_presets": {lang: preset(lang) for lang in others},
            "tts": tts,
            "conversation": {"max_duration_seconds": MAX_CALL_SECONDS},
        },
        "platform_settings": {
            # Without this, anyone who knows the agent id could talk to it on your account.
            "auth": {"enable_auth": True},
            "overrides": {
                "conversation_config_override": {
                    "agent": {"prompt": {"prompt": True}, "first_message": True, "language": True},
                    # Rehearsals run the agent in text.
                    "conversation": {"text_only": True},
                }
            },
            # Transcript only: no audio is stored at ElevenLabs.
            "privacy": {"record_voice": False},
        },
    }


def setup_agent(
    settings: Settings, name: str = "Uhura", transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """Create the agent, or update the configured one. Returns what ElevenLabs stored."""
    api = _Api(settings, transport)
    config = agent_config(name, settings.llm, settings.voice_id, settings.language_voices)
    if settings.elevenlabs_agent_id:
        agent_id = settings.elevenlabs_agent_id
        api("PATCH", f"/v1/convai/agents/{agent_id}", json=config)
    else:
        agent_id = api("POST", "/v1/convai/agents/create", json=config)["agent_id"]
    return api("GET", f"/v1/convai/agents/{agent_id}")


def tool_configs(public_url: str, secret_id: str, ask_timeout: float) -> list[dict[str, Any]]:
    """The webhook tools the agent uses to reach the Uhura service mid-call."""
    base = public_url.rstrip("/")
    call_id = {"type": "string", "dynamic_variable": "uhura_call_id"}

    def webhook(name: str, description: str, timeout: int, extra: dict[str, Any], **options: Any) -> dict:
        return {
            "type": "webhook",
            "name": name,
            "description": description,
            "response_timeout_secs": timeout,
            "api_schema": {
                "url": f"{base}/agent-tools/{name}",
                "method": "POST",
                "request_headers": {"X-Uhura-Secret": {"secret_id": secret_id}},
                "request_body_schema": {
                    "type": "object",
                    "properties": {"call_id": call_id, **extra},
                    "required": ["call_id", *extra],
                },
            },
            **options,
        }

    return [
        webhook(
            "ask_principal",
            "Ask the person you are calling for a question you cannot answer from your briefing, "
            "or when a decision is needed. Tell the other party you are checking briefly before "
            "you call this. The reply can take up to a minute.",
            # The service gives up after ask_timeout; leave ElevenLabs some slack on top.
            min(int(ask_timeout) + 20, 300),
            {
                "question": {
                    "type": "string",
                    "description": "The question, self-contained, in the language the goal of the call is written in.",
                }
            },
            pre_tool_speech="force",
            tool_call_sound="typing",
        ),
        webhook(
            "final_check",
            "Call this once, just before you say goodbye, to receive any last instructions.",
            20,
            {},
        ),
        webhook(
            "report_progress",
            "Report how the call is going. Use it only if your instructions ask for progress reports.",
            5,
            {
                "stage": {"type": "string", "enum": list(STAGES), "description": "Where the call is now."},
                "note": {
                    "type": "string",
                    "description": "One short sentence in the language of the call: what just happened.",
                },
            },
            # Nobody on the line should notice: no waiting, no filler speech, no sound.
            execution_mode="async",
            pre_tool_speech="off",
        ),
        webhook(
            "consent_refused",
            "Call this as soon as the person objects to the call or to its transcription, "
            "before you apologise and end the call.",
            # Not async: the call often ends right after, and the service must have heard it.
            5,
            {},
            pre_tool_speech="off",
        ),
    ]


def _sync_secret(api: _Api, value: str) -> str:
    """Store the tool secret in ElevenLabs' secret store (not in the tool config) and return its id."""
    for secret in api("GET", "/v1/convai/secrets").get("secrets", []):
        if secret["name"] == SECRET_NAME:
            api(
                "PATCH",
                f"/v1/convai/secrets/{secret['secret_id']}",
                json={"type": "update", "name": SECRET_NAME, "value": value},
            )
            return secret["secret_id"]
    return api("POST", "/v1/convai/secrets", json={"type": "new", "name": SECRET_NAME, "value": value})["secret_id"]


def setup_tools(settings: Settings, public_url: str, transport: httpx.BaseTransport | None = None) -> dict[str, str]:
    """Create or update the agent's tools so they point at `public_url`. Returns name -> tool id."""
    if not public_url.startswith("https://"):
        raise SetupError("the public address must start with https://")
    if not settings.elevenlabs_agent_id:
        raise SetupError("ELEVENLABS_AGENT_ID is not set; run `uhura setup-agent` first")
    if not settings.tool_secret:
        raise SetupError("UHURA_TOOL_SECRET is not set")
    try:
        check_secrets(settings)
    except WeakSecretError as exc:
        raise SetupError(str(exc)) from None
    api = _Api(settings, transport)
    secret_id = _sync_secret(api, settings.tool_secret)
    existing = {t["tool_config"]["name"]: t["id"] for t in api("GET", "/v1/convai/tools").get("tools", [])}
    ids: dict[str, str] = {}
    for config in tool_configs(public_url, secret_id, settings.ask_timeout):
        name = config["name"]
        if name in existing:
            api("PATCH", f"/v1/convai/tools/{existing[name]}", json={"tool_config": config})
            ids[name] = existing[name]
        else:
            ids[name] = api("POST", "/v1/convai/tools", json={"tool_config": config})["id"]
    api(
        "PATCH",
        f"/v1/convai/agents/{settings.elevenlabs_agent_id}",
        json={"conversation_config": {"agent": {"prompt": {"tool_ids": list(ids.values())}}}},
    )
    return ids


def ngrok_url(api_url: str = "http://127.0.0.1:4040/api/tunnels") -> str:
    """The https address of the ngrok tunnel running on this machine."""
    try:
        tunnels = httpx.get(api_url, timeout=3).json()["tunnels"]
    except (httpx.HTTPError, KeyError, ValueError):
        raise SetupError("no ngrok tunnel found on this machine; start one with `ngrok http 8787`") from None
    for tunnel in tunnels:
        if tunnel.get("public_url", "").startswith("https://"):
            return tunnel["public_url"]
    raise SetupError("ngrok is running but has no https tunnel")
