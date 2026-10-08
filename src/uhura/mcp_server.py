"""MCP tools exposing Uhura to any MCP client.

The same tools are served two ways: by the service itself at `/mcp` (streamable HTTP),
and by `uhura-mcp`, a local stdio adapter that forwards to the service over HTTP. Either
way each tool goes through the service's HTTP API, so tokens, ownership and guardrails
apply exactly as for the CLI.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from .api_client import AsyncUhuraClient, UhuraError

INSTRUCTIONS = (
    "Places phone calls through an AI voice agent on the user's behalf. "
    "Always draft_call first and show the user the number, opening line and briefing; "
    "only confirm_call after the user has approved that specific call. "
    "After confirming, call wait_for_event in a loop until the call has ended. "
    "When a 'question' event arrives, answer it only from what the user has told you; "
    "otherwise ask the user. Answers are needed within about 90 seconds. "
    "To try a draft without dialling, use rehearse_call: you play the person being called "
    "with rehearse_say and read the agent's replies with wait_for_event."
)

ClientFor = Callable[[Context], AsyncUhuraClient]


def build(client_for: ClientFor) -> MCPServer:
    """An MCP server whose tools reach the service through `client_for(ctx)`."""
    mcp = MCPServer("uhura", instructions=INSTRUCTIONS)

    async def _call(ctx: Context, fn: Callable[[AsyncUhuraClient], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        async with client_for(ctx) as client:
            try:
                return await fn(client)
            except UhuraError as exc:
                return {"error": str(exc)}

    @mcp.tool()
    async def draft_call(
        ctx: Context,
        to: str,
        principal: str,
        goal: str,
        language: str = "de",
        facts: list[str] | None = None,
        must_not: list[str] | None = None,
        region: str = "DE",
        voicemail: str | None = None,
        topic: str | None = None,
        progress: bool | None = None,
    ) -> dict[str, Any]:
        """Prepare a call without dialling. Returns the call id, the normalised number,
        the fixed opening line, the voicemail message and the full briefing the voice agent
        will receive. `voicemail` is what to say if a mailbox answers; a fixed AI
        introduction is spoken before it. Without it the agent hangs up on voicemail.
        `topic` is a short phrase completing "Es geht um …" / "It is about …" (e.g. "eine
        Reiseanfrage"); the agent says it in its fixed re-introduction to the first person
        it reaches after a phone menu or queue. No full stops or question marks.
        `progress` switches the agent's progress reports ('progress' events) on or off for
        this call; by default the service decides."""
        brief = {
            "to": to,
            "principal": principal,
            "goal": goal,
            "language": language,
            "facts": facts or [],
            "must_not": must_not or [],
            "region": region,
            "voicemail": voicemail,
            "topic": topic,
            "progress": progress,
        }
        return await _call(ctx, lambda c: c.draft(brief))

    @mcp.tool()
    async def confirm_call(ctx: Context, call_id: str) -> dict[str, Any]:
        """Dial a drafted call. Only use after the user approved this specific draft."""
        return await _call(ctx, lambda c: c.confirm(call_id))

    @mcp.tool()
    async def wait_for_event(ctx: Context, call_id: str, after: int = 0, timeout: float = 25) -> dict[str, Any]:
        """Wait up to `timeout` seconds for new events on a call (questions from the agent,
        'progress' reports with `stage` and `note`, call ended). Pass the highest `seq` seen
        so far as `after`. Returns an empty list if nothing happened; call again until status
        is 'done' or 'failed'."""
        return await _call(ctx, lambda c: c.events(call_id, after=after, timeout=timeout))

    @mcp.tool()
    async def answer(ctx: Context, call_id: str, question_id: int, text: str) -> dict[str, Any]:
        """Answer a question the voice agent asked mid-call. `question_id` is the event's `seq`."""
        return await _call(ctx, lambda c: c.answer(call_id, question_id, text))

    @mcp.tool()
    async def send_instruction(ctx: Context, call_id: str, text: str) -> dict[str, Any]:
        """Queue a follow-up instruction for a running call. It reaches the agent with its
        next tool call, at the latest just before it hangs up, so delivery is not immediate."""
        return await _call(ctx, lambda c: c.instruct(call_id, text))

    @mcp.tool()
    async def get_call(ctx: Context, call_id: str) -> dict[str, Any]:
        """Status, duration, transcript and cost of a call. `cost` (once the call has ended)
        has the ElevenLabs credits charged, the billed minutes after the silence discount,
        and ElevenLabs' dollar prices for the voice platform and the language model.
        If the person called refused consent, `consent_refused_at` is set and there is no
        transcript."""
        return await _call(ctx, lambda c: c.show(call_id))

    @mcp.tool()
    async def list_calls(ctx: Context, limit: int = 20) -> dict[str, Any]:
        """The user's recent calls, newest first."""
        return await _call(ctx, lambda c: c.list_calls(limit))

    @mcp.tool()
    async def rehearse_call(ctx: Context, call_id: str) -> dict[str, Any]:
        """Start a rehearsal of a drafted call: the real agent with the real briefing, in text,
        with nobody being called. Read what the agent says with wait_for_event, passing the
        returned `events_after` as `after` ('agent_said' events), and reply as the person
        being called with rehearse_say. Questions and instructions work as in a real call."""
        return await _call(ctx, lambda c: c.rehearse(call_id))

    @mcp.tool()
    async def rehearse_say(ctx: Context, call_id: str, text: str) -> dict[str, Any]:
        """Say something to the agent in a running rehearsal, as the person being called."""
        return await _call(ctx, lambda c: c.rehearsal_say(call_id, text))

    @mcp.tool()
    async def end_rehearsal(ctx: Context, call_id: str) -> dict[str, Any]:
        """End a rehearsal. The draft stays unchanged and can still be confirmed."""
        return await _call(ctx, lambda c: c.end_rehearsal(call_id))

    return mcp


# The stdio adapter: UHURA_URL and UHURA_TOKEN from the environment, as for the CLI.
mcp = build(lambda ctx: AsyncUhuraClient())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
