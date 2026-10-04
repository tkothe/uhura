import asyncio

import pytest

from uhura import cli, phrases
from uhura.cli import build_parser, format_rehearsal_event
from uhura.mcp_server import mcp


class FakeClient:
    def __init__(self):
        self.drafted = None

    def draft(self, brief):
        self.drafted = brief
        return {
            "id": "abc",
            "to_number": "+493023125000",
            "first_message": "Guten Tag",
            "reintroduction": "Guten Tag, ich bin ein KI-Assistent",
            "prompt": "line 1\nline 2",
        }

    def list_calls(self, limit):
        return {
            "calls": [
                {"id": "abc", "created_at": 0, "status": "done", "to_number": "+493023125000", "duration_secs": 42}
            ]
        }

    def show(self, call_id):
        return {
            "id": call_id,
            "to_number": "+493023125000",
            "status": "failed",
            "error": "busy",
            "transcript": [{"role": "agent", "message": "Guten Tag"}],
        }

    def instruct(self, call_id, text):
        return {"message": phrases.INSTRUCTION_QUEUED}


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(cli, "UhuraClient", lambda: fake)
    return fake


def test_draft_collects_repeatable_options_and_shows_next_steps(client, capsys):
    cli.main(
        ["draft", "--to", "030 23125 000", "--principal", "Erika", "--goal", "G", "--fact", "a", "--fact", "b"]
        + ["--topic", "eine Reiseanfrage"]
    )
    assert client.drafted == {
        "to": "030 23125 000",
        "language": "de",
        "principal": "Erika",
        "topic": "eine Reiseanfrage",
        "goal": "G",
        "facts": ["a", "b"],
        "must_not": [],
        "region": "DE",
        "voicemail": None,
        "progress": None,
    }
    out = capsys.readouterr().out
    assert phrases.DRAFTED in out and "    line 2" in out
    assert "after menu: Guten Tag, ich bin ein KI-Assistent" in out
    assert "uhura rehearse abc" in out and "uhura confirm abc" in out


def test_list_show_and_say(client, capsys):
    cli.main(["list"])
    cli.main(["show", "abc"])
    cli.main(["say", "abc", "Ask about parking"])
    out = capsys.readouterr().out
    assert "abc  1970-01-0" in out and "42 s" in out
    assert "call abc to +493023125000: failed" in out and "error: busy" in out and "agent: Guten Tag" in out
    assert phrases.INSTRUCTION_QUEUED in out


def test_watching_a_draft_explains_instead_of_waiting_forever(monkeypatch, capsys):
    class Draft:
        def show(self, call_id):
            return {"status": "draft"}

    monkeypatch.setattr(cli, "UhuraClient", Draft)
    cli.main(["watch", "abc"])
    assert "uhura confirm abc" in capsys.readouterr().out


def test_service_errors_exit_with_the_message(monkeypatch):
    class Broken:
        def show(self, call_id):
            raise cli.UhuraError("no such call")

    monkeypatch.setattr(cli, "UhuraClient", Broken)
    with pytest.raises(SystemExit, match="no such call"):
        cli.main(["show", "nope"])


def test_every_command_is_wired_to_a_handler():
    parser = build_parser()
    commands = parser._subparsers._group_actions[0].choices
    assert set(commands) == {
        "serve", "setup-agent", "setup-tools", "check", "draft", "rehearse",
        "confirm", "watch", "say", "show", "list",
    }  # fmt: skip
    assert all(callable(sub.get_default("func")) for sub in commands.values())


@pytest.mark.parametrize(
    "event, expected",
    [
        ({"type": "agent_said", "text": "Guten Tag"}, "agent> Guten Tag"),
        ({"type": "tool_used", "name": "final_check", "ok": True}, "(agent used final_check)"),
        ({"type": "rehearsal_ended", "message": "over"}, "over"),
        ({"type": "callee_said", "text": "echo of my own line"}, None),
    ],
)
def test_rehearsal_events_are_formatted_for_the_terminal(event, expected):
    assert format_rehearsal_event(event) == expected


def test_rehearsal_question_tells_how_to_answer():
    line = format_rehearsal_event({"type": "question", "question": "Which day?"})
    assert "Which day?" in line and "/answer" in line


def test_mcp_server_exposes_the_documented_tools():
    listed = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in getattr(listed, "tools", listed)}
    assert names == {
        "draft_call", "confirm_call", "wait_for_event", "answer", "send_instruction",
        "get_call", "list_calls", "rehearse_call", "rehearse_say", "end_rehearsal",
    }  # fmt: skip


def test_progress_flag_and_event_formatting():
    args = build_parser().parse_args(["draft", "--to", "1", "--principal", "E", "--goal", "G", "--no-progress"])
    assert args.progress is False
    assert format_rehearsal_event({"type": "progress", "stage": "hold", "note": "Position 4"}) == "[hold] Position 4"
