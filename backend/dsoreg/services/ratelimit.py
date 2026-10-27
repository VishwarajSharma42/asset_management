"""Fixed-window rate limiting kept in the database, so it is correct however many gunicorn
workers run (an in-process counter would make the real limit N times looser)."""
from __future__ import annotations

import random
import time

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from ..db import rate_limit
from ..errors import TooManyRequests


def hit(engine: Engine, key: str, limit: int, window_s: int, *, now: float | None = None) -> None:
    """Count one event against `key`; raise 429 once `limit` is exceeded in the window."""
    now = int(now if now is not None else time.time())
    window = now - (now % window_s)
    with engine.begin() as conn:
        res = conn.execute(update(rate_limit)
                           .where(rate_limit.c.key == key, rate_limit.c.window_start == window)
                           .values(count=rate_limit.c.count + 1))
        if res.rowcount == 0:
            conn.execute(delete(rate_limit).where(rate_limit.c.key == key))
            try:
                with conn.begin_nested():
                    conn.execute(rate_limit.insert().values(key=key, window_start=window, count=1))
            except IntegrityError:
                conn.execute(update(rate_limit).where(rate_limit.c.key == key)
                             .values(count=rate_limit.c.count + 1))
        count = conn.execute(select(rate_limit.c.count).where(rate_limit.c.key == key)).scalar() or 1
        if random.random() < 0.01:
            conn.execute(delete(rate_limit).where(rate_limit.c.window_start < now - 7200))
    if count > limit:
        raise TooManyRequests(retry_after=max(1, window + window_s - now))
