"""`uhura check`: verify a setup end to end, as far as that is possible without dialling.

Static checks look at the configuration and the ElevenLabs account. The optional live
rehearsal runs a scripted text conversation against the real agent and checks that the
briefing, a question to the principal and a follow-up instruction all arrive.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from .api_client import UhuraClient, UhuraError
from .config import Settings, weak_secrets
from .elevenlabs import BASE_URL
from .setup_agent import BUILT_IN_TOOLS

Result = tuple[str, bool, str]  # what was checked, passed, detail

# A number from the range reserved for fiction; the rehearsal never dials it anyway.
TEST_BRIEF = {
    "to": "+49 30 23125 000",
    "language": "de",
    "principal": "Erika Musterfrau",
    "goal": "Fragen, ob der Laden am Freitag geöffnet hat.",
    "facts": ["Erika Musterfrau möchte am Freitag vorbeikommen."],
}


def check_account(settings: Settings, transport: httpx.BaseTransport | None = None) -> Iterator[Result]:
    """The ElevenLabs side: key, agent settings, tools and whether they can reach the service."""
    http = httpx.Client(
        base_url=BASE_URL, headers={"xi-api-key": settings.elevenlabs_api_key}, timeout=30, transport=transport
    )
    resp = http.get("/v1/user/subscription")
    yield "ElevenLabs API key works", resp.status_code == 200, f"HTTP {resp.status_code}"
    if resp.status_code != 200:
        return
    if not settings.elevenlabs_agent_id:
        yield "agent configured", False, "ELEVENLABS_AGENT_ID is empty; run `uhura setup-agent`"
        return
    resp = http.get(f"/v1/convai/agents/{settings.elevenlabs_agent_id}")
    yield "agent exists", resp.status_code == 200, settings.elevenlabs_agent_id
    if resp.status_code != 200:
        return
    agent = resp.json()
    platform = agent.get("platform_settings", {})
    prompt = agent["conversation_config"]["agent"]["prompt"]
    allowed = platform.get("overrides", {}).get("conversation_config_override", {})
    allowed_agent = allowed.get("agent", {})
    yield "no audio is stored", platform.get("privacy", {}).get("record_voice") is False, ""
    yield "agent requires authentication", bool(platform.get("auth", {}).get("enable_auth")), ""
    yield (
        "per-call briefing, opening line and language allowed",
        bool(
            allowed_agent.get("prompt", {}).get("prompt")
            and allowed_agent.get("first_message")
            and allowed_agent.get("language")
        ),
        "",
    )
    built_in = [name for name, config in (prompt.get("built_in_tools") or {}).items() if config]
    yield "agent can hang up", "end_call" in built_in, ", ".join(t for t in BUILT_IN_TOOLS if t in built_in)
    yield (
        "agent can press keys in phone menus and wait silently on hold",
        {"play_keypad_touch_tone", "skip_turn"} <= set(built_in),
        "run `uhura setup-agent` to add them" if not {"play_keypad_touch_tone", "skip_turn"} <= set(built_in) else "",
    )

    tools = {}
    for tool_id in prompt.get("tool_ids") or []:
        resp = http.get(f"/v1/convai/tools/{tool_id}")
        if resp.status_code == 200:
            config = resp.json()["tool_config"]
            tools[config["name"]] = config
    for name in ("ask_principal", "final_check", "report_progress", "consent_refused"):
        yield f"tool {name} attached", name in tools, ""
    for base in sorted({t["api_schema"]["url"].split("/agent-tools/")[0] for t in tools.values()}):
        try:
            ok = httpx.get(f"{base}/health", timeout=10).json().get("status") == "ok"
        except (httpx.HTTPError, ValueError):
            ok = False
        yield "tools can reach this service", ok, base

    if settings.elevenlabs_phone_number_id:
        resp = http.get(f"/v1/convai/phone-numbers/{settings.elevenlabs_phone_number_id}")
        yield "phone number exists", resp.status_code == 200, settings.elevenlabs_phone_number_id
    else:
        yield "phone number configured", False, "ELEVENLABS_PHONE_NUMBER_ID is empty; real calls are not possible yet"


class _Rehearsal:
    """Drives one scripted rehearsal through the service API."""

    def __init__(self, client: UhuraClient, call_id: str, after: int):
        self.client, self.call_id, self.after = client, call_id, after
        self.seen: list[dict[str, Any]] = []

    def wait_for(self, kind: str, timeout: float = 60) -> dict[str, Any] | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            events = self.client.events(self.call_id, after=self.after, timeout=10)["events"]
            for event in events:
                self.after = event["seq"]
                self.seen.append(event)
                if event["type"] == kind:
                    return event
                if event["type"] == "rehearsal_ended":
                    return None
        return None

    def said(self, needle: str) -> bool:
        return any(needle.lower() in e.get("text", "").lower() for e in self.seen if e["type"] == "agent_said")


def check_rehearsal(client: UhuraClient) -> Iterator[Result]:
    """A scripted conversation with the real agent. Uses a few ElevenLabs credits."""
    try:
        call = client.draft(TEST_BRIEF)
        started = client.rehearse(call["id"])
    except UhuraError as exc:
        yield "rehearsal starts", False, str(exc)
        return
    run = _Rehearsal(client, call["id"], started["events_after"])
    try:
        opening = run.wait_for("agent_said")
        yield (
            "agent opens with the fixed disclosure",
            bool(opening and "Erika Musterfrau" in opening["text"] and "KI-Assistent" in opening["text"]),
            (opening or {}).get("text", "no reply")[:80],
        )
        client.rehearsal_say(call["id"], "Ja, das ist in Ordnung. Hier ist der Laden, was kann ich für Sie tun?")
        goal = run.wait_for("agent_said")
        yield "agent follows the briefing", bool(goal and "Freitag" in goal["text"]), (goal or {}).get("text", "")[:80]

        client.rehearsal_say(
            call["id"],
            "Das kann ich Ihnen gleich sagen. Vorher brauche ich bitte die Kundennummer von Frau Musterfrau.",
        )
        question = run.wait_for("question", timeout=90)
        yield (
            "agent asks the principal when it lacks a fact",
            question is not None,
            (question or {}).get("question", "")[:80],
        )
        if question:
            client.answer(call["id"], question["seq"], "Die Kundennummer ist 4711.")
            for _ in range(3):
                if run.said("4711") or run.wait_for("agent_said", timeout=45) is None:
                    break
            yield "the answer reaches the conversation", run.said("4711"), ""

        client.instruct(call["id"], "Frag zusätzlich, ob der Laden auch am Samstag geöffnet hat.")
        client.rehearsal_say(call["id"], "Danke. Ja, am Freitag haben wir von 9 bis 18 Uhr geöffnet. Auf Wiederhören.")
        for _ in range(4):
            if run.said("Samstag") or run.wait_for("agent_said", timeout=45) is None:
                break
        used_final_check = any(e["type"] == "tool_used" and e.get("name") == "final_check" for e in run.seen)
        yield "agent checks for last instructions before hanging up", used_final_check, ""
        yield "a follow-up instruction reaches the conversation", run.said("Samstag"), ""
    finally:
        try:
            client.end_rehearsal(call["id"])
        except UhuraError:
            pass


def run_checks(rehearsal: bool = False) -> bool:
    settings = Settings.from_env()
    client = UhuraClient()
    results: list[Result] = []

    def report(result: Result) -> None:
        results.append(result)
        name, ok, detail = result
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail else ""))

    print("Configuration")
    weak = weak_secrets(settings)
    report(("tokens and tool secret are strong", not weak, ", ".join(weak)))
    print("Service")
    try:
        client.list_calls(1)
        report(("service reachable and token accepted", True, ""))
    except UhuraError as exc:
        report(("service reachable and token accepted", False, str(exc)))
    print("ElevenLabs account")
    for result in check_account(settings):
        report(result)
    if rehearsal:
        print("Live rehearsal")
        for result in check_rehearsal(client):
            report(result)
    return all(ok for _, ok, _ in results)
