"""Small request helpers shared by the route modules. No business logic here."""
from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timezone
from functools import wraps

from flask import current_app, g, request
from sqlalchemy import select

from .context import Ctx
from .db import users
from .errors import BadRequest, Forbidden, PayloadTooLarge, Unauthorized
from .security.tokens import ROLE_SCOPES
from .security.util import ip_hash
from .services import ratelimit


def ctx() -> Ctx:
    return current_app.extensions["dsoreg"]


def now() -> datetime:
    return datetime.now(timezone.utc)


def client_ip() -> str:
    return request.remote_addr or "0.0.0.0"


def hashed_ip() -> str:
    return ip_hash(ctx().settings.ip_hash_seed, client_ip(), now().strftime("%Y-%m-%d"))


def limit(key: str, per: int, window_s: int) -> None:
    ratelimit.hit(ctx().engine, key, per, window_s)


def require_campus_network() -> None:
    nets = ctx().settings.scan_allowed_cidrs
    if not nets:
        return
    try:
        addr = ipaddress.ip_address(client_ip())
    except ValueError:
        raise Forbidden("Please connect to the campus network.", code="network_not_allowed") from None
    if not any(addr in n for n in nets):
        raise Forbidden("Please connect to the campus network.", code="network_not_allowed")


SMALL_BODY = 32 * 1024


def json_body(max_bytes: int = SMALL_BODY) -> dict:
    """Parse a JSON object body. Only the enrolment and photo-edit endpoints may be large;
    everything else is capped at 32 KB, so the public endpoints cannot be fed megabytes."""
    if not request.is_json:
        raise BadRequest("Send application/json.", code="bad_content_type")
    if request.content_length is not None and request.content_length > max_bytes:
        raise PayloadTooLarge()
    try:
        body = json.loads(request.get_data(cache=True))
    except (ValueError, UnicodeDecodeError):
        raise BadRequest("Body is not valid JSON.") from None
    if not isinstance(body, dict):
        raise BadRequest("Body must be a JSON object.")
    return body


def require(scope: str):
    """Authenticate a staff request and check it carries `scope`. The role is read from the
    database on every call, so demoting or disabling a user takes effect immediately."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                raise Unauthorized()
            c = ctx()
            claims = c.sessions.verify(header[7:].strip())
            with c.engine.connect() as conn:
                row = conn.execute(select(users).where(users.c.id == claims["sub"])).first()
            if row is None or not row.is_active or row.token_epoch != claims["ep"]:
                raise Unauthorized("Session is no longer valid.", code="invalid_token")
            if scope not in ROLE_SCOPES.get(row.role, []):
                raise Forbidden()
            g.user = {"id": row.id, "username": row.username, "role": row.role,
                      "display_name": row.display_name}
            limit(f"staff:{row.id}", 600, 60)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def actor() -> str:
    return f"user:{g.user['username']}"
