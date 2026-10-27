"""Hash-chained audit log.

    hash = SHA-256( prev_hash || canonical_json({ts, actor, event, subject, detail}) )

Rewriting, deleting or inserting any row breaks every hash after it, and `verify()` says
where. The chain head can be copied somewhere outside the database; if the head you
copied last week is no longer in the chain, history was altered.

Every console mutation calls `append()` INSIDE the same transaction as the change, so
there is no way to change state without leaving a link.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.engine import Connection

from ..db import audit_log

GENESIS = "0" * 64


def _canon(entry: dict) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _hash(prev: str, entry: dict) -> str:
    return hashlib.sha256(prev.encode() + b"\x00" + _canon(entry)).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def append(conn: Connection, *, actor: str, event: str, subject: str | None = None,
           detail: dict | None = None) -> str:
    if conn.dialect.name == "postgresql":
        conn.execute(text("SELECT pg_advisory_xact_lock(727001)"))
    head = conn.execute(select(audit_log.c.hash).order_by(audit_log.c.seq.desc()).limit(1)).scalar()
    prev = head or GENESIS
    detail_json = json.dumps(detail or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    ts = _now_iso()
    entry = {"ts": ts, "actor": actor, "event": event, "subject": subject, "detail": detail_json}
    digest = _hash(prev, entry)
    conn.execute(audit_log.insert().values(ts=ts, actor=actor, event=event, subject=subject,
                                           detail=detail_json, prev_hash=prev, hash=digest))
    return digest


def verify(conn: Connection) -> dict:
    prev = GENESIS
    count = 0
    for row in conn.execute(select(audit_log).order_by(audit_log.c.seq)):
        entry = {"ts": row.ts, "actor": row.actor, "event": row.event,
                 "subject": row.subject, "detail": row.detail}
        if row.prev_hash != prev or row.hash != _hash(prev, entry):
            return {"ok": False, "checked": count, "broken_at_seq": row.seq, "head": None}
        prev = row.hash
        count += 1
    return {"ok": True, "checked": count, "broken_at_seq": None, "head": prev}


def page(conn: Connection, *, limit: int, before_seq: int | None) -> list[dict]:
    q = select(audit_log).order_by(audit_log.c.seq.desc()).limit(limit)
    if before_seq:
        q = q.where(audit_log.c.seq < before_seq)
    out = []
    for r in conn.execute(q):
        out.append({"seq": r.seq, "ts": r.ts, "actor": r.actor, "event": r.event,
                    "subject": r.subject, "detail": json.loads(r.detail), "hash": r.hash})
    return out
