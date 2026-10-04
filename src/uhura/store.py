"""SQLite persistence for calls, their event log and queued instructions."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    status TEXT NOT NULL,
    brief TEXT NOT NULL,
    to_number TEXT NOT NULL,
    first_message TEXT NOT NULL,
    prompt TEXT NOT NULL,
    conversation_id TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    duration_secs INTEGER,
    transcript TEXT,
    error TEXT,
    cost TEXT
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS instructions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    text TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
"""

JSON_COLUMNS = ("brief", "transcript", "cost")
# Columns added after the first release; older databases get them on start.
ADDED_COLUMNS = {"cost": "TEXT"}
# A call in one of these states is in use and must not be purged.
ACTIVE = ("dialling", "in_progress", "rehearsing")


def _assignments(fields: dict[str, Any]) -> tuple[str, list[Any]]:
    """`col = ?, …` and its values for an UPDATE, with JSON columns encoded."""
    values = [json.dumps(v) if k in JSON_COLUMNS and v is not None else v for k, v in fields.items()]
    return ", ".join(f"{k} = ?" for k in fields), values


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            existing = {r["name"] for r in self._db.execute("PRAGMA table_info(calls)")}
            for column, kind in ADDED_COLUMNS.items():
                if column not in existing:
                    self._db.execute(f"ALTER TABLE calls ADD COLUMN {column} {kind}")
            self._db.commit()

    # The connection is shared between the event loop and FastAPI's worker threads, so
    # every statement runs, and is fully read, while holding the lock.

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple = ()) -> int:
        """Run a write and return the id of the inserted row, if any."""
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur.lastrowid

    # --- calls ---

    def create_call(self, user: str, brief: dict, to_number: str, first_message: str, prompt: str) -> str:
        call_id = uuid.uuid4().hex[:12]
        self._execute(
            "INSERT INTO calls (id, user, status, brief, to_number, first_message, prompt, created_at)"
            " VALUES (?, ?, 'draft', ?, ?, ?, ?, ?)",
            (call_id, user, json.dumps(brief), to_number, first_message, prompt, time.time()),
        )
        return call_id

    def get_call(self, call_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM calls WHERE id = ?", (call_id,))
        if not rows:
            return None
        call = dict(rows[0])
        for col in JSON_COLUMNS:
            call[col] = json.loads(call[col]) if call[col] else None
        return call

    def update_call(self, call_id: str, **fields: Any) -> None:
        sets, values = _assignments(fields)
        self._execute(f"UPDATE calls SET {sets} WHERE id = ?", (*values, call_id))

    def claim_call(self, call_id: str, expected: str, **fields: Any) -> bool:
        """Update a call only if its status is still `expected`. False if another request got there first."""
        sets, values = _assignments(fields)
        with self._lock:
            cur = self._db.execute(f"UPDATE calls SET {sets} WHERE id = ? AND status = ?", (*values, call_id, expected))
            self._db.commit()
            return cur.rowcount == 1

    def list_calls(self, user: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT id, status, to_number, created_at, started_at, duration_secs,"
            " json_extract(cost, '$.credits') AS credits FROM calls"
            " WHERE user = ? ORDER BY created_at DESC LIMIT ?",
            (user, limit),
        )
        return [dict(r) for r in rows]

    def calls_with_status(self, status: str) -> list[dict[str, Any]]:
        rows = self._query("SELECT id FROM calls WHERE status = ?", (status,))
        return [self.get_call(r["id"]) for r in rows]

    def purge(self, older_than: float) -> int:
        """Delete calls created before `older_than`, with their events and instructions.

        Calls still in use (dialling, in progress, rehearsing) are kept whatever their age.
        """
        with self._lock:
            ids = [
                r["id"]
                for r in self._db.execute(
                    f"SELECT id FROM calls WHERE created_at < ? AND status NOT IN ({', '.join('?' * len(ACTIVE))})",
                    (older_than, *ACTIVE),
                )
            ]
            for table, column in (("events", "call_id"), ("instructions", "call_id"), ("calls", "id")):
                self._db.executemany(f"DELETE FROM {table} WHERE {column} = ?", [(i,) for i in ids])
            self._db.commit()
        return len(ids)

    def usage(self, user: str, now: float | None = None, running_secs: int = 0) -> tuple[int, int]:
        """(calls started in the last 24 h, seconds used in the last 30 days) for a user.

        A call still dialling or in progress has no duration yet; it counts as `running_secs`.
        """
        now = now or time.time()
        calls_today = self._query("SELECT COUNT(*) FROM calls WHERE user = ? AND started_at > ?", (user, now - 86400))[
            0
        ][0]
        seconds = self._query(
            "SELECT COALESCE(SUM(CASE WHEN status IN ('dialling', 'in_progress') THEN ?"
            " ELSE COALESCE(duration_secs, 0) END), 0) FROM calls WHERE user = ? AND started_at > ?",
            (running_secs, user, now - 30 * 86400),
        )[0][0]
        return calls_today, seconds

    def rehearsals_since(self, user: str, since: float) -> int:
        """How many rehearsals a user has started since `since`."""
        return self._query(
            "SELECT COUNT(*) FROM events JOIN calls ON calls.id = events.call_id"
            " WHERE calls.user = ? AND events.type = 'rehearsal_started' AND events.at > ?",
            (user, since),
        )[0][0]

    # --- events ---

    def add_event(self, call_id: str, type_: str, data: dict) -> int:
        return self._execute(
            "INSERT INTO events (call_id, type, data, at) VALUES (?, ?, ?, ?)",
            (call_id, type_, json.dumps(data), time.time()),
        )

    def last_seq(self, call_id: str) -> int:
        return self._query("SELECT COALESCE(MAX(seq), 0) FROM events WHERE call_id = ?", (call_id,))[0][0]

    def last_event(self, call_id: str, type_: str) -> dict[str, Any] | None:
        """The newest event of one type on a call, or None."""
        rows = self._query(
            "SELECT data FROM events WHERE call_id = ? AND type = ? ORDER BY seq DESC LIMIT 1", (call_id, type_)
        )
        return json.loads(rows[0]["data"]) if rows else None

    def events_after(self, call_id: str, after: int) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT seq, type, data, at FROM events WHERE call_id = ? AND seq > ? ORDER BY seq",
            (call_id, after),
        )
        return [{"seq": r["seq"], "type": r["type"], "at": r["at"], **json.loads(r["data"])} for r in rows]

    # --- instructions ---

    def queue_instruction(self, call_id: str, text: str) -> None:
        self._execute("INSERT INTO instructions (call_id, text) VALUES (?, ?)", (call_id, text))

    def take_instructions(self, call_id: str) -> list[str]:
        """Return the undelivered instructions for a call and mark them delivered."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text FROM instructions WHERE call_id = ? AND delivered = 0 ORDER BY id",
                (call_id,),
            ).fetchall()
            self._db.execute("UPDATE instructions SET delivered = 1 WHERE call_id = ? AND delivered = 0", (call_id,))
            self._db.commit()
        return [r["text"] for r in rows]
