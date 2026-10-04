"""The Uhura HTTP service: draft and confirm calls, relay questions, collect transcripts."""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from . import mcp_server, phrases
from .api_client import AsyncUhuraClient
from .config import Settings, check_secrets
from .disclosures import UnsupportedLanguage, disclosure, reintroduction, voicemail_message
from .elevenlabs import ElevenLabsClient, TextSession, VoiceClient
from .guardrails import GuardrailError, check_budget, check_rehearsals, normalise_number
from .models import STAGES, Answer, Brief, Instruction, Utterance
from .prompt import build_prompt
from .setup_agent import MAX_CALL_SECONDS
from .store import Store

log = logging.getLogger("uhura")

FINISHED = {"done", "failed"}
# Statuses in which the voice agent may call its tools. A call is `dialling` from the
# moment it is confirmed until ElevenLabs has accepted it.
LIVE = {"dialling", "in_progress", "rehearsing"}
PURGE_INTERVAL = 6 * 3600
# What the agent reads back from report_progress. Without it, a bare result made the agent
# take another turn and send the same report again (seen in rehearsals).
PROGRESS_DONE = "Done. Nothing else to do for this report; do not send it again. Continue the conversation."
NO_ANSWER = (
    "No answer arrived in time. Say that the person you are calling for will follow up by email, "
    "then continue with the rest of the call."
)


class CallManager:
    """Call lifecycle plus the in-memory plumbing for waiting on events and answers."""

    def __init__(self, settings: Settings, store: Store, client: VoiceClient):
        self.settings = settings
        self.store = store
        self.client = client
        self._changed: dict[str, asyncio.Condition] = {}
        self._answers: dict[int, tuple[str, asyncio.Future[str]]] = {}  # question id -> call id, answer
        self._rehearsals: dict[str, TextSession] = {}
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background task %s failed", task.get_coro().__qualname__, exc_info=task.exception())

    def _condition(self, call_id: str) -> asyncio.Condition:
        return self._changed.setdefault(call_id, asyncio.Condition())

    async def _notify(self, call_id: str) -> None:
        cond = self._condition(call_id)
        async with cond:
            cond.notify_all()

    async def emit(self, call_id: str, type_: str, **data: Any) -> int:
        seq = self.store.add_event(call_id, type_, data)
        await self._notify(call_id)
        return seq

    # --- service lifecycle ---

    async def startup(self) -> None:
        """Pick up where a previous process left off."""
        for call in self.store.calls_with_status("rehearsing"):
            self.store.update_call(call["id"], status="draft")
        for call in self.store.calls_with_status("dialling"):
            # Whether ElevenLabs accepted the call is unknown; never dial it a second time.
            self.store.update_call(call["id"], status="failed", error="the service stopped while dialling")
            await self.emit(call["id"], "call_ended", status="failed", message=phrases.NO_RESPONSE)
        for call in self.store.calls_with_status("in_progress"):
            if not call["conversation_id"]:
                # Nothing to ask ElevenLabs about; left over from an interrupted start.
                self.store.update_call(call["id"], status="failed", error="the call never reached ElevenLabs")
                await self.emit(call["id"], "call_ended", status="failed", message=phrases.NO_RESPONSE)
                continue
            started_at = call["started_at"] or call["created_at"]
            self._spawn(self._watch(call["id"], call["conversation_id"], started_at))
        self._spawn(self._purge_loop())

    async def shutdown(self) -> None:
        for session in list(self._rehearsals.values()):
            await session.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def purge(self, now: float | None = None) -> int:
        if self.settings.retention_days <= 0:
            return 0
        return self.store.purge((now or time.time()) - self.settings.retention_days * 86400)

    async def _purge_loop(self) -> None:
        while True:
            removed = self.purge()
            if removed:
                log.info("deleted %d calls older than %d days", removed, self.settings.retention_days)
            await asyncio.sleep(PURGE_INTERVAL)

    # --- call lifecycle ---

    def draft(self, user: str, brief: Brief) -> dict[str, Any]:
        to = normalise_number(brief.to, brief.region, self.settings)
        first_message = disclosure(brief.language, brief.principal)
        voicemail_message(brief.language, brief.principal, brief.voicemail)  # refuse unsupported languages now
        if brief.progress is None:
            brief = brief.model_copy(update={"progress": self.settings.progress})
        call_id = self.store.create_call(user, brief.model_dump(), to, first_message, build_prompt(brief))
        return self.show(call_id)

    def show(self, call_id: str) -> dict[str, Any] | None:
        """A stored call plus the voicemail message and re-introduction it would use, for review."""
        call = self.store.get_call(call_id)
        if call is not None:
            brief = call["brief"]
            call["voicemail_message"] = voicemail_message(brief["language"], brief["principal"], brief.get("voicemail"))
            call["reintroduction"] = reintroduction(brief["language"], brief["principal"], brief.get("topic"))
        return call

    def _briefing(self, call: dict[str, Any]) -> dict[str, str]:
        return {
            "prompt": call["prompt"],
            "first_message": call["first_message"],
            "language": call["brief"]["language"],
            "call_id": call["id"],
            "voicemail": self.show(call["id"])["voicemail_message"],
        }

    def _claim(self, call: dict[str, Any], refusal: str, **fields: Any) -> None:
        """Move a draft on, or refuse if another request already did. Two requests for the
        same draft must never both dial or both open a rehearsal."""
        if not self.store.claim_call(call["id"], "draft", **fields):
            raise GuardrailError(f"{refusal}; this call is {self.store.get_call(call['id'])['status']}")

    async def confirm(self, call: dict[str, Any]) -> dict[str, Any]:
        if call["status"] != "draft":
            raise GuardrailError(f"call is already {call['status']}")
        # Budget check and claim run without yielding to the event loop, and the claim sets
        # started_at, so concurrent confirms of other drafts count this one. Calls without a
        # result yet, this one included, count at the longest the agent will talk.
        usage = self.store.usage(call["user"], running_secs=MAX_CALL_SECONDS)
        check_budget(*usage, self.settings, call_secs=MAX_CALL_SECONDS)
        started_at = time.time()
        self._claim(call, "only a draft can be dialled", status="dialling", started_at=started_at)
        try:
            conversation_id = await self.client.start_call(to=call["to_number"], **self._briefing(call))
        except Exception as exc:
            # A call that never started does not count against the daily limit.
            self.store.update_call(call["id"], status="failed", error=str(exc), started_at=None)
            await self.emit(call["id"], "call_ended", status="failed", message=phrases.NO_RESPONSE)
            raise
        self.store.update_call(call["id"], status="in_progress", conversation_id=conversation_id)
        await self.emit(call["id"], "call_started", message=phrases.OPEN)
        self._spawn(self._watch(call["id"], conversation_id, started_at))
        return self.store.get_call(call["id"])

    async def _watch(self, call_id: str, conversation_id: str, started_at: float) -> None:
        """Wait for the call's result and store it. Whatever goes wrong here, the call ends:
        a call left `in_progress` keeps its clients waiting for good."""
        try:
            conv = await self._result(conversation_id, started_at)
            self.store.update_call(
                call_id,
                status=conv["status"],
                transcript=conv["transcript"],
                duration_secs=conv["duration_secs"] or 0,
                error=conv.get("error") if conv["status"] == "failed" else None,
                cost=conv.get("cost"),
            )
            status = conv["status"]
        except Exception as exc:
            log.exception("could not store the result of call %s", call_id)
            self.store.update_call(call_id, status="failed", error=f"Uhura could not store the result: {exc}")
            status = "failed"
        message = phrases.CLOSED if status == "done" else phrases.NO_RESPONSE
        await self.emit(call_id, "call_ended", status=status, message=message)

    async def _result(self, conversation_id: str, started_at: float) -> dict[str, Any]:
        """Poll the provider until the call is over; give up after `max_call_seconds`."""
        deadline = started_at + self.settings.max_call_seconds
        while True:
            await asyncio.sleep(self.settings.poll_interval)
            try:
                conv = await self.client.get_conversation(conversation_id)
            except Exception:
                log.exception("polling conversation %s failed; retrying", conversation_id)
                conv = None
            if conv and conv["status"] in FINISHED:
                return conv
            if time.time() > deadline:
                return {
                    "status": "failed",
                    "transcript": (conv or {}).get("transcript"),
                    # The call may have run; count the time against the budget, not zero.
                    "duration_secs": min(int(time.time() - started_at), MAX_CALL_SECONDS),
                    "error": f"no result from the provider after {self.settings.max_call_seconds} s",
                }

    # --- rehearsal: the same briefing against the real agent, in text, without dialling ---

    async def start_rehearsal(self, call: dict[str, Any]) -> dict[str, Any]:
        # As for confirm: the check, the claim and the event that counts this rehearsal all
        # happen before the first await, so concurrent starts count each other.
        check_rehearsals(self.store.rehearsals_since(call["user"], time.time() - 86400), self.settings)
        self._claim(call, "only a draft can be rehearsed", status="rehearsing")
        after = self.store.last_seq(call["id"])
        self.store.add_event(call["id"], "rehearsal_started", {"message": phrases.REHEARSAL_OPEN})
        try:
            session = await self.client.open_text_session(**self._briefing(call))
        except Exception:
            self.store.update_call(call["id"], status="draft")
            await self.emit(call["id"], "rehearsal_ended", message=phrases.NO_RESPONSE)
            raise
        self._rehearsals[call["id"]] = session
        await self._notify(call["id"])
        self._spawn(self._run_rehearsal(call["id"], session))
        return {**self.store.get_call(call["id"]), "events_after": after}

    async def _run_rehearsal(self, call_id: str, session: TextSession) -> None:
        ended: dict[str, Any] = {}
        try:
            # A rehearsal nobody ends (a client that went away) would keep using credits.
            async with asyncio.timeout(self.settings.max_rehearsal_seconds):
                async for event in session:
                    await self.emit(call_id, event.pop("type"), **event)
        except TimeoutError:
            await session.close()
            ended["reason"] = f"time limit of {self.settings.max_rehearsal_seconds} s reached"
        finally:
            self._rehearsals.pop(call_id, None)
            if (self.store.get_call(call_id) or {}).get("status") == "rehearsing":
                self.store.update_call(call_id, status="draft")
            await self.emit(call_id, "rehearsal_ended", message=phrases.REHEARSAL_CLOSED, **ended)

    async def rehearsal_say(self, call_id: str, text: str) -> None:
        session = self._rehearsals.get(call_id)
        if session is None:
            raise GuardrailError("no rehearsal is running for this call")
        await session.send(text)
        await self.emit(call_id, "callee_said", text=text)

    async def stop_rehearsal(self, call_id: str) -> None:
        session = self._rehearsals.get(call_id)
        if session is not None:
            await session.close()

    # --- steering ---

    async def wait_events(self, call_id: str, after: int, timeout: float) -> list[dict[str, Any]]:
        events = self.store.events_after(call_id, after)
        if events or timeout <= 0:
            return events
        cond = self._condition(call_id)
        try:
            async with cond:
                await asyncio.wait_for(cond.wait(), timeout)
        except TimeoutError:
            pass
        return self.store.events_after(call_id, after)

    async def ask(self, call_id: str, question: str) -> dict[str, Any]:
        """Called by the voice agent mid-call; holds the line until someone answers."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        # Register the future before waiters are woken, so a fast answer cannot miss it.
        seq = self.store.add_event(call_id, "question", {"question": question, "message": phrases.QUESTION})
        self._answers[seq] = (call_id, future)
        await self._notify(call_id)
        try:
            answer = await asyncio.wait_for(future, self.settings.ask_timeout)
        except TimeoutError:
            await self.emit(call_id, "question_expired", question_id=seq)
            answer = None
        finally:
            self._answers.pop(seq, None)
        return self._tool_result(call_id, answer=answer or NO_ANSWER, answered=answer is not None)

    async def answer(self, call_id: str, question_id: int, text: str) -> bool:
        asked_in, future = self._answers.get(question_id, (None, None))
        # The question must belong to this call: the caller was only checked against call_id.
        if asked_in != call_id or future is None or future.done():
            return False
        future.set_result(text)
        await self.emit(call_id, "answered", question_id=question_id, answer=text)
        return True

    async def instruct(self, call_id: str, text: str) -> None:
        self.store.queue_instruction(call_id, text)
        await self.emit(call_id, "instruction_queued", text=text)

    def _tool_result(self, call_id: str, **result: Any) -> dict[str, Any]:
        """Every tool result carries any instructions queued since the last one."""
        result["new_instructions"] = self.store.take_instructions(call_id)
        return result

    def final_check(self, call_id: str) -> dict[str, Any]:
        return self._tool_result(call_id)

    async def report_progress(self, call_id: str, stage: str, note: str) -> bool:
        """Record a progress report, if this call asked for them. The prompt only asks for
        reports on such calls; this is the check that does not depend on the model."""
        if not (self.store.get_call(call_id) or {}).get("brief", {}).get("progress"):
            return False
        # ElevenLabs sometimes delivers the same report twice in a row (seen on a real call).
        if self.store.last_event(call_id, "progress") == {"stage": stage, "note": note}:
            return False
        await self.emit(call_id, "progress", stage=stage, note=note)
        return True


def bearer(authorization: str) -> str:
    return authorization.removeprefix("Bearer ").strip()


class RequireToken:
    """Refuses requests without a known user token before they reach the wrapped app."""

    def __init__(self, app: ASGIApp, tokens: dict[str, str]):
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if bearer(Request(scope).headers.get("authorization", "")) not in self.tokens:
            await JSONResponse({"detail": "unknown token"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None, client: VoiceClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    check_secrets(settings)  # refuse to serve with placeholder or short credentials
    manager = CallManager(settings, Store(settings.db_path), client or ElevenLabsClient(settings))

    # MCP tools served at /mcp. Each tool calls this app's own API in-process with the
    # caller's token, so it acts as that user, with the same checks as the CLI.
    mcp = mcp_server.build(
        lambda ctx: AsyncUhuraClient(
            url="http://uhura",
            token=bearer((ctx.headers or {}).get("authorization", "")),
            transport=httpx.ASGITransport(app=app),
        )
    )
    mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        # DNS-rebinding protection guards local servers without authentication. Every
        # request here needs a user token, which a web page cannot supply; left on, the
        # check would also refuse a hosted instance's own host name.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await manager.startup()
        async with mcp.session_manager.run():
            yield
        await manager.shutdown()

    app = FastAPI(title="Uhura", description=phrases.OPEN, lifespan=lifespan)
    app.state.manager = manager

    def user(authorization: str = Header(default="")) -> str:
        name = settings.tokens.get(bearer(authorization))
        if not name:
            raise HTTPException(401, "unknown token")
        return name

    def own_call(call_id: str, name: str = Depends(user)) -> dict[str, Any]:
        call = manager.store.get_call(call_id)
        if call is None or call["user"] != name:
            raise HTTPException(404, "no such call")
        return call

    def agent_tool(x_uhura_secret: str = Header(default="")) -> None:
        if not settings.tool_secret or not hmac.compare_digest(x_uhura_secret, settings.tool_secret):
            raise HTTPException(401, "bad tool secret")

    def live_call(body: dict[str, Any]) -> str:
        call = manager.store.get_call(str(body.get("call_id", "")))
        if call is None or call["status"] not in LIVE:
            raise HTTPException(404, "no such running call")
        return call["id"]

    app.add_route(
        "/mcp",
        RequireToken(mcp.session_manager.handle_request, settings.tokens),
        methods=["GET", "POST", "DELETE"],
        include_in_schema=False,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "message": phrases.STANDING_BY}

    @app.get("/calls")
    def list_calls(limit: int = 20, name: str = Depends(user)) -> dict[str, Any]:
        return {"calls": manager.store.list_calls(name, min(limit, 100))}

    @app.post("/calls")
    def draft(brief: Brief, name: str = Depends(user)) -> dict[str, Any]:
        try:
            call = manager.draft(name, brief)
        except (GuardrailError, UnsupportedLanguage) as exc:
            raise HTTPException(422, f"{phrases.REFUSED} {exc}") from None
        return {**call, "message": phrases.DRAFTED}

    @app.post("/calls/{call_id}/confirm")
    async def confirm(call: dict = Depends(own_call)) -> dict[str, Any]:
        try:
            call = await manager.confirm(call)
        except GuardrailError as exc:
            raise HTTPException(409, f"{phrases.REFUSED} {exc}") from None
        except Exception as exc:
            raise HTTPException(502, f"{phrases.NO_RESPONSE} ({exc})") from None
        return {**call, "message": phrases.OPEN}

    @app.get("/calls/{call_id}")
    def show(call: dict = Depends(own_call)) -> dict[str, Any]:
        return manager.show(call["id"])

    @app.get("/calls/{call_id}/events")
    async def events(after: int = 0, timeout: float = 25, call: dict = Depends(own_call)) -> dict[str, Any]:
        found = await manager.wait_events(call["id"], after, min(timeout, 55))
        return {"events": found, "status": manager.store.get_call(call["id"])["status"]}

    @app.post("/calls/{call_id}/answer")
    async def answer(body: Answer, call: dict = Depends(own_call)) -> dict[str, Any]:
        if not await manager.answer(call["id"], body.question_id, body.text):
            raise HTTPException(409, "that question is no longer waiting for an answer")
        return {"delivered": True}

    @app.post("/calls/{call_id}/instructions")
    async def instruct(body: Instruction, call: dict = Depends(own_call)) -> dict[str, Any]:
        if call["status"] not in LIVE:
            raise HTTPException(409, f"call is {call['status']}, not in progress")
        await manager.instruct(call["id"], body.text)
        return {"queued": True, "message": phrases.INSTRUCTION_QUEUED}

    @app.post("/calls/{call_id}/rehearsal")
    async def start_rehearsal(call: dict = Depends(own_call)) -> dict[str, Any]:
        try:
            call = await manager.start_rehearsal(call)
        except GuardrailError as exc:
            raise HTTPException(409, str(exc)) from None
        except Exception as exc:
            raise HTTPException(502, f"{phrases.NO_RESPONSE} ({exc})") from None
        return {**call, "message": phrases.REHEARSAL_OPEN}

    @app.post("/calls/{call_id}/rehearsal/say")
    async def rehearsal_say(body: Utterance, call: dict = Depends(own_call)) -> dict[str, Any]:
        try:
            await manager.rehearsal_say(call["id"], body.text)
        except GuardrailError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"sent": True}

    @app.delete("/calls/{call_id}/rehearsal")
    async def stop_rehearsal(call: dict = Depends(own_call)) -> dict[str, Any]:
        await manager.stop_rehearsal(call["id"])
        return {"stopped": True, "message": phrases.REHEARSAL_CLOSED}

    # --- called by the voice agent, not by users ---

    @app.post("/agent-tools/ask_principal", dependencies=[Depends(agent_tool)])
    async def ask_principal(body: dict[str, Any]) -> dict[str, Any]:
        question = str(body.get("question", "")).strip()
        if not question:
            raise HTTPException(422, "question is required")
        return await manager.ask(live_call(body), question)

    @app.post("/agent-tools/report_progress", dependencies=[Depends(agent_tool)])
    async def report_progress(body: dict[str, Any]) -> dict[str, Any]:
        stage = str(body.get("stage", ""))
        if stage not in STAGES:
            raise HTTPException(422, f"stage must be one of {', '.join(STAGES)}")
        note = str(body.get("note", "")).strip()[:500]
        recorded = await manager.report_progress(live_call(body), stage, note)
        return {"recorded": recorded, "next": PROGRESS_DONE}

    @app.post("/agent-tools/final_check", dependencies=[Depends(agent_tool)])
    def final_check(body: dict[str, Any]) -> dict[str, Any]:
        return manager.final_check(live_call(body))

    return app
