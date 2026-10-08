import asyncio

import httpx
import pytest

from uhura.config import Settings
from uhura.service import create_app

TOKEN = "erika-test-token-0123456789"
OTHER_TOKEN = "max-test-token-0123456789"
SECRET = "tool-secret-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
OTHER = {"Authorization": f"Bearer {OTHER_TOKEN}"}
TOOL = {"X-Uhura-Secret": SECRET}
BRIEF = {"to": "030 23125 000", "principal": "Erika Musterfrau", "goal": "Ask for opening hours"}


class FakeTextSession:
    """A rehearsal partner the test controls: `agent_says()` and `hang_up()`."""

    def __init__(self):
        self.heard: list[str] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send(self, text):
        self.heard.append(text)

    def agent_says(self, text):
        self._queue.put_nowait({"type": "agent_said", "text": text})

    def hang_up(self):
        self._queue.put_nowait(None)

    async def __aiter__(self):
        while (event := await self._queue.get()) is not None:
            yield event

    async def close(self):
        self.hang_up()


class FakeVoice:
    """Stands in for ElevenLabs: a call stays in progress until `finish()`."""

    def __init__(self):
        self.started: list[dict] = []
        self.status = "in-progress"
        self.fail_with: Exception | None = None
        self.session: FakeTextSession | None = None
        self.rehearsals: list[dict] = []
        self.delay = 0.0  # seconds the provider takes to accept a call or rehearsal
        self.error = None  # what get_conversation reports as the error
        self.deleted: list[str] = []
        self.delete_fails = False

    async def start_call(self, **kwargs):
        await asyncio.sleep(self.delay)
        if self.fail_with:
            raise self.fail_with
        self.started.append(kwargs)
        return "conv_1"

    async def get_conversation(self, conversation_id):
        return {
            "status": self.status,
            "transcript": [{"role": "agent", "message": "Guten Tag"}],
            "duration_secs": 42,
            "error": self.error,
            "cost": {"credits": 900, "billed_minutes": 0.7, "platform_usd": 0.09, "llm_usd": 0.01},
        }

    async def delete_conversation(self, conversation_id):
        if self.delete_fails:
            raise RuntimeError("missing_permissions")
        self.deleted.append(conversation_id)

    async def open_text_session(self, **kwargs):
        await asyncio.sleep(self.delay)
        if self.fail_with:
            raise self.fail_with
        self.rehearsals.append(kwargs)
        self.session = FakeTextSession()
        return self.session

    def finish(self, status="done"):
        self.status = status


def make_settings(**overrides) -> Settings:
    values = dict(
        tokens={TOKEN: "erika", OTHER_TOKEN: "max"},
        tool_secret=SECRET,
        db_path=":memory:",
        poll_interval=0.01,
        ask_timeout=5,  # generous: an answer arriving late must not fail unrelated tests
    )
    values.update(overrides)
    return Settings(**values)


def make(**overrides):
    """An app wired to a fake voice provider, with an HTTP client for it."""
    voice = FakeVoice()
    app = create_app(make_settings(**overrides), voice)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://uhura")
    client.manager = app.state.manager
    return client, voice


async def until(predicate, tries=200):
    """Poll an async predicate until it returns something truthy."""
    for _ in range(tries):
        if result := await predicate():
            return result
        await asyncio.sleep(0.01)
    raise AssertionError("condition was never met")


@pytest.fixture
def run():
    return asyncio.run
