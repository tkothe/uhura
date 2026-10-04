"""HTTP clients for the Uhura service, shared by the CLI and the MCP tools."""

from __future__ import annotations

import os
from typing import Any

import httpx

from .config import load_dotenv


class UhuraError(RuntimeError):
    pass


def _settings(url: str | None, token: str | None) -> dict[str, Any]:
    """Connection settings; `url` and `token` default to UHURA_URL and UHURA_TOKEN."""
    load_dotenv()
    if token is None:
        token = os.environ.get("UHURA_TOKEN", "")
    return {
        "base_url": url or os.environ.get("UHURA_URL", "http://localhost:8787"),
        "headers": {"Authorization": f"Bearer {token}"},
        "timeout": 70,
    }


def _result(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise UhuraError(str(detail))
    return resp.json()


class _Endpoints:
    """The service's endpoints. Each method returns what `_request` returns: the result
    for the blocking client, an awaitable for the async one."""

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    def draft(self, brief: dict[str, Any]) -> Any:
        return self._request("POST", "/calls", json=brief)

    def confirm(self, call_id: str) -> Any:
        return self._request("POST", f"/calls/{call_id}/confirm")

    def show(self, call_id: str) -> Any:
        return self._request("GET", f"/calls/{call_id}")

    def events(self, call_id: str, after: int = 0, timeout: float = 25) -> Any:
        return self._request("GET", f"/calls/{call_id}/events", params={"after": after, "timeout": timeout})

    def answer(self, call_id: str, question_id: int, text: str) -> Any:
        return self._request("POST", f"/calls/{call_id}/answer", json={"question_id": question_id, "text": text})

    def instruct(self, call_id: str, text: str) -> Any:
        return self._request("POST", f"/calls/{call_id}/instructions", json={"text": text})

    def list_calls(self, limit: int = 20) -> Any:
        return self._request("GET", "/calls", params={"limit": limit})

    def rehearse(self, call_id: str) -> Any:
        return self._request("POST", f"/calls/{call_id}/rehearsal")

    def rehearsal_say(self, call_id: str, text: str) -> Any:
        return self._request("POST", f"/calls/{call_id}/rehearsal/say", json={"text": text})

    def end_rehearsal(self, call_id: str) -> Any:
        return self._request("DELETE", f"/calls/{call_id}/rehearsal")


class UhuraClient(_Endpoints):
    """Blocking client, for the CLI."""

    def __init__(self, url: str | None = None, token: str | None = None):
        self._http = httpx.Client(**_settings(url, token))

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise UhuraError(f"cannot reach the Uhura service: {exc}") from None
        return _result(resp)


class AsyncUhuraClient(_Endpoints):
    """Async client, for the MCP tools. `transport` lets the service call itself in-process."""

    def __init__(
        self, url: str | None = None, token: str | None = None, transport: httpx.AsyncBaseTransport | None = None
    ):
        self._http = httpx.AsyncClient(**_settings(url, token), transport=transport)

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise UhuraError(f"cannot reach the Uhura service: {exc}") from None
        return _result(resp)

    async def __aenter__(self) -> AsyncUhuraClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()
