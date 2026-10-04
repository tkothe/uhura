"""The MCP tools as served by the service at /mcp, over streamable HTTP."""

from contextlib import asynccontextmanager

import httpx
import httpx2
from conftest import AUTH, BRIEF, OTHER_TOKEN, TOKEN, FakeVoice, make_settings
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from uhura.service import create_app


@asynccontextmanager
async def serving():
    """The app with its lifespan running, as under uvicorn."""
    voice = FakeVoice()
    app = create_app(make_settings(), voice)
    async with app.router.lifespan_context(app):
        yield app, voice


def mcp_client(app, token):
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers={"Authorization": f"Bearer {token}"})
    return Client(streamable_http_client("http://uhura/mcp", http_client=http))


async def call(client, tool, **arguments):
    result = await client.call_tool(tool, arguments)
    return result.structured_content


def test_mcp_needs_a_known_token(run):
    async def scenario():
        async with serving() as (app, _):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://uhura") as http:
                for headers in ({}, {"Authorization": "Bearer nope"}):
                    resp = await http.post("/mcp", headers=headers, json={})
                    assert resp.status_code == 401

    run(scenario())


def test_mcp_tools_act_as_the_token_holder(run):
    async def scenario():
        async with serving() as (app, voice):
            async with mcp_client(app, TOKEN) as erika:
                tools = {tool.name for tool in (await erika.list_tools()).tools}
                assert {"draft_call", "confirm_call", "wait_for_event"} <= tools

                draft = await call(erika, "draft_call", **BRIEF)
                assert draft["status"] == "draft" and draft["user"] == "erika"
                assert not voice.started

                events = await call(erika, "wait_for_event", call_id=draft["id"], timeout=0)
                assert events["status"] == "draft"

            async with mcp_client(app, OTHER_TOKEN) as max_:
                assert (await call(max_, "list_calls"))["calls"] == []
                assert "no such call" in (await call(max_, "get_call", call_id=draft["id"]))["error"]

            # The REST API sees the same call, owned by the same user.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://uhura") as http:
                listed = (await http.get("/calls", headers=AUTH)).json()["calls"]
                assert [c["id"] for c in listed] == [draft["id"]]

    run(scenario())
