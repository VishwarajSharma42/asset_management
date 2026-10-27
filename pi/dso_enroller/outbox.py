"""The durable queue. A record is on disk (WAL, synchronous=FULL) BEFORE any network call and
before the operator is told it worked, so a pulled plug or a dead network cannot lose a
tag that has already been written."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    idem_key     TEXT NOT NULL UNIQUE,
    body         BLOB,
    name         TEXT NOT NULL,
    room_no      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    state        TEXT NOT NULL DEFAULT 'pending',   -- pending | sent | failed
    attempts     INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL NOT NULL DEFAULT 0,
    last_error   TEXT,
    asset_id     TEXT
);
CREATE INDEX IF NOT EXISTS ix_outbox_state ON outbox(state, next_attempt);
"""


class Outbox:
    def __init__(self, path: Path | str):
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def enqueue(self, body: dict, *, name: str, room_no: str) -> int:
        raw = json.dumps(body, separators=(",", ":")).encode()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO outbox (idem_key, body, name, room_no, created_at) VALUES (?,?,?,?,?)",
                (uuid.uuid4().hex, raw, name, room_no, time.time()))
            return cur.lastrowid

    def due(self, now: float | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM outbox WHERE state='pending' AND next_attempt<=? ORDER BY id LIMIT 20",
                (now if now is not None else time.time(),)).fetchall()

    def mark_sent(self, row_id: int, asset_id: str | None) -> None:
        with self._lock:
            self._db.execute("UPDATE outbox SET state='sent', body=NULL, asset_id=?, last_error=NULL WHERE id=?",
                             (asset_id, row_id))

    def retry_later(self, row_id: int, error: str, *, attempts: int) -> None:
        delay = min(3600, 5 * 2 ** min(attempts, 10))
        with self._lock:
            self._db.execute("UPDATE outbox SET attempts=?, next_attempt=?, last_error=? WHERE id=?",
                             (attempts, time.time() + delay, error[:300], row_id))

    def mark_failed(self, row_id: int, error: str) -> None:
        with self._lock:
            self._db.execute("UPDATE outbox SET state='failed', last_error=? WHERE id=?", (error[:300], row_id))

    def requeue_failed(self) -> int:
        with self._lock:
            cur = self._db.execute("UPDATE outbox SET state='pending', next_attempt=0 WHERE state='failed'")
            return cur.rowcount

    def stats(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT state, COUNT(*) AS n FROM outbox GROUP BY state").fetchall()
        out = {"pending": 0, "failed": 0, "sent": 0}
        out.update({r["state"]: r["n"] for r in rows})
        return out

    def recent(self, limit: int = 12) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, name, room_no, created_at, state, attempts, last_error FROM outbox "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._db.close()
