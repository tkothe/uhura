"""Command-line client for Uhura. Run `uhura --help`."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time

from . import phrases
from .api_client import UhuraClient, UhuraError

ENDED = ("done", "failed")


def _print_draft(call: dict) -> None:
    print(phrases.DRAFTED)
    print(f"  call id : {call['id']}")
    print(f"  number  : {call['to_number']}")
    print(f"  opening : {call['first_message']}")
    if call.get("reintroduction"):  # older services do not send it
        print(f"  after menu: {call['reintroduction']}")
    print(f"  voicemail: {call.get('voicemail_message') or '(none: hangs up on voicemail)'}")
    print("  briefing:")
    for line in call["prompt"].splitlines():
        print(f"    {line}")
    print(f"\nTry it without dialling: uhura rehearse {call['id']}")
    print(f"Transmit with:           uhura confirm {call['id']}")


def _print_call(call: dict) -> None:
    print(f"call {call['id']} to {call['to_number']}: {call['status']}", end="")
    print(f" ({call['duration_secs']} s)" if call.get("duration_secs") else "")
    if call.get("error"):
        print(f"  error: {call['error']}")
    if cost := call.get("cost"):
        usd = sum(v for v in (cost.get("platform_usd"), cost.get("llm_usd")) if v)
        print(f"  cost: {cost['credits']} credits, {cost.get('billed_minutes')} billed min, about ${usd:.2f}")
    for turn in call.get("transcript") or []:
        print(f"  {turn['role']:>5}: {turn['message']}")


# --- setup ---


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .config import WeakSecretError
    from .service import create_app

    try:
        app = create_app()
    except WeakSecretError as exc:
        sys.exit(f"Not starting: {exc}.")
    print(phrases.STANDING_BY)
    uvicorn.run(app, host=args.host, port=args.port)


def cmd_setup_agent(args: argparse.Namespace) -> None:
    from .config import Settings
    from .setup_agent import SetupError, setup_agent

    settings = Settings.from_env()
    try:
        agent = setup_agent(settings, args.name)
    except SetupError as exc:
        sys.exit(str(exc))
    if settings.elevenlabs_agent_id:
        print(f"Agent {agent['agent_id']} updated.")
    else:
        print(f"Agent created. Add this to .env:\n  ELEVENLABS_AGENT_ID={agent['agent_id']}")


def cmd_setup_tools(args: argparse.Namespace) -> None:
    from .config import Settings
    from .setup_agent import SetupError, ngrok_url, setup_tools

    try:
        url = args.url or ngrok_url()
        ids = setup_tools(Settings.from_env(), url)
    except SetupError as exc:
        sys.exit(str(exc))
    print(f"The agent's tools now point at {url}")
    for name, tool_id in ids.items():
        print(f"  {name}: {tool_id}")


def cmd_check(args: argparse.Namespace) -> None:
    from .check import run_checks

    if not run_checks(rehearsal=args.rehearsal):
        sys.exit(1)


# --- calls ---


def cmd_draft(args: argparse.Namespace) -> None:
    brief = {
        "to": args.to,
        "language": args.language,
        "principal": args.principal,
        "topic": args.topic,
        "goal": args.goal,
        "facts": args.fact,
        "must_not": args.must_not,
        "region": args.region,
        "voicemail": args.voicemail,
        "progress": args.progress,
    }
    _print_draft(UhuraClient().draft(brief))


def cmd_confirm(args: argparse.Namespace) -> None:
    client = UhuraClient()
    call = client.confirm(args.call_id)
    print(call["message"])
    if not args.no_watch:
        watch(client, args.call_id)


def watch(client: UhuraClient, call_id: str) -> None:
    """Follow a call: print events, prompt for answers to the agent's questions."""
    if client.show(call_id)["status"] == "draft":
        print(f"This call has not been dialled yet. Transmit with: uhura confirm {call_id}")
        return
    after = 0
    while True:
        result = client.events(call_id, after=after)
        for event in result["events"]:
            after = event["seq"]
            if event["type"] == "question":
                print(f"\n{phrases.QUESTION}\n  {event['question']}")
                reply = input("  your answer> ").strip()
                if reply:
                    try:
                        client.answer(call_id, event["seq"], reply)
                    except UhuraError as exc:
                        print(f"  ({exc})")
            elif event["type"] == "call_ended":
                print(f"\n{event['message']}")
            elif event["type"] == "question_expired":
                print("  (the agent stopped waiting for that answer)")
            elif event["type"] == "progress":
                print(f"  [{event['stage']}] {event['note']}")
        if result["status"] in ENDED:
            break
    _print_call(client.show(call_id))


def cmd_watch(args: argparse.Namespace) -> None:
    watch(UhuraClient(), args.call_id)


def cmd_say(args: argparse.Namespace) -> None:
    print(UhuraClient().instruct(args.call_id, args.text)["message"])


def cmd_show(args: argparse.Namespace) -> None:
    call = UhuraClient().show(args.call_id)
    if args.json:
        print(json.dumps(call, indent=2, ensure_ascii=False))
    else:
        _print_call(call)


def cmd_list(args: argparse.Namespace) -> None:
    for call in UhuraClient().list_calls(args.limit)["calls"]:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(call["created_at"]))
        duration = f"{call['duration_secs']} s" if call.get("duration_secs") else ""
        credits = f"{call['credits']} credits" if call.get("credits") is not None else ""
        print(f"{call['id']}  {when}  {call['status']:<11} {call['to_number']:<16} {duration:<7} {credits}")


# --- rehearsal ---

REHEARSAL_HELP = """\
You play the person being called. Type what they would say and press Enter.
  /answer <text>   answer the agent's last question, as the person it calls for
  /say <text>      queue a follow-up instruction for the agent
  /quit            end the rehearsal"""


def format_rehearsal_event(event: dict) -> str | None:
    """One printable line per rehearsal event, or None for events not worth showing."""
    kind = event["type"]
    if kind == "agent_said":
        return f"agent> {event['text']}"
    if kind == "question":
        return f"{phrases.QUESTION}\n  {event['question']}\n  (reply with /answer <text>)"
    if kind == "question_expired":
        return "(the agent stopped waiting for that answer)"
    if kind == "progress":
        return f"[{event['stage']}] {event['note']}"
    if kind == "tool_used":
        return f"(agent used {event['name']})"
    if kind == "rehearsal_ended":
        return event["message"]
    return None


def cmd_rehearse(args: argparse.Namespace) -> None:
    client = UhuraClient()
    started = client.rehearse(args.call_id)
    print(f"{started['message']}\n{REHEARSAL_HELP}\n")
    ended = threading.Event()
    last_question: list[int] = []

    def follow() -> None:
        after = started["events_after"]
        while not ended.is_set():
            try:
                events = client.events(args.call_id, after=after, timeout=10)["events"]
            except UhuraError as exc:
                print(f"({exc})")
                ended.set()
                return
            for event in events:
                after = event["seq"]
                if event["type"] == "question":
                    last_question[:] = [event["seq"]]
                line = format_rehearsal_event(event)
                if line:
                    print(f"\n{line}")
                if event["type"] == "rehearsal_ended":
                    ended.set()

    follower = threading.Thread(target=follow, daemon=True)
    follower.start()
    try:
        while not ended.is_set():
            line = input().strip()
            if ended.is_set() or line == "/quit":
                break
            if not line:
                continue
            try:
                if line.startswith("/answer "):
                    if not last_question:
                        print("(no question is waiting)")
                    else:
                        client.answer(args.call_id, last_question[0], line.removeprefix("/answer ").strip())
                elif line.startswith("/say "):
                    print(client.instruct(args.call_id, line.removeprefix("/say ").strip())["message"])
                else:
                    client.rehearsal_say(args.call_id, line)
            except UhuraError as exc:
                print(f"({exc})")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        if not ended.is_set():
            client.end_rehearsal(args.call_id)
            follower.join(timeout=15)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uhura", description=phrases.OPEN)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the service")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("setup-agent", help="create or update the caller agent in your ElevenLabs account")
    p.add_argument("--name", default="Uhura")
    p.set_defaults(func=cmd_setup_agent)

    p = sub.add_parser("setup-tools", help="point the agent's tools at this service's public address")
    p.add_argument("--url", help="public https address; default: the ngrok tunnel on this machine")
    p.set_defaults(func=cmd_setup_tools)

    p = sub.add_parser("check", help="verify the setup: service, ElevenLabs account, agent, tools")
    p.add_argument("--rehearsal", action="store_true", help="also run a scripted text conversation with the agent")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("draft", help="prepare a call and show what the agent will be told")
    p.add_argument("--to", required=True, help="number to call")
    p.add_argument("--principal", required=True, help="person the agent calls on behalf of")
    p.add_argument("--goal", required=True)
    p.add_argument("--topic", help="what the call is about, completing 'Es geht um …', e.g. 'eine Reiseanfrage'")
    p.add_argument("--language", default="de")
    p.add_argument("--fact", action="append", default=[], help="fact the agent may share (repeatable)")
    p.add_argument("--must-not", action="append", default=[], help="extra limit (repeatable)")
    p.add_argument("--region", default="DE", help="country for numbers without +prefix")
    p.add_argument("--voicemail", help="message to leave if a mailbox answers (default: hang up)")
    p.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        help="whether the agent reports progress during the call (default: UHURA_PROGRESS)",
    )
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser("rehearse", help="try a drafted call in text against the real agent, without dialling")
    p.add_argument("call_id")
    p.set_defaults(func=cmd_rehearse)

    p = sub.add_parser("confirm", help="dial a drafted call and follow it")
    p.add_argument("call_id")
    p.add_argument("--no-watch", action="store_true")
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser("watch", help="follow a running call and answer its questions")
    p.add_argument("call_id")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("say", help="queue a follow-up instruction for a running call")
    p.add_argument("call_id")
    p.add_argument("text")
    p.set_defaults(func=cmd_say)

    p = sub.add_parser("show", help="show status and transcript")
    p.add_argument("call_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("list", help="list your recent calls")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except UhuraError as exc:
        sys.exit(str(exc))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
