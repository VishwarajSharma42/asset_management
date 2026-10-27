"""Staff accounts: local Argon2id passwords, lock-out after repeated failures, roles."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import validators as v
from ..context import Ctx
from ..db import users
from ..errors import BadRequest, Conflict, NotFound, TooManyRequests, Unauthorized
from ..security import passwords
from ..security.tokens import ROLE_SCOPES
from . import audit
from .common import iso

MAX_FAILURES = 5
LOCK_BASE = timedelta(minutes=5)
GENERIC = "Invalid username or password."


def create_user(ctx: Ctx, *, username, display_name, role, password, actor: str, now: datetime) -> dict:
    uname = v.username(username)
    if role not in ROLE_SCOPES:
        raise BadRequest("role must be admin or viewer.", code="invalid_field")
    display = v.person_name(display_name)
    passwords.check_policy(password if isinstance(password, str) else "", uname)
    uid = str(uuid.uuid4())
    try:
        with ctx.engine.begin() as conn:
            conn.execute(users.insert().values(
                id=uid, username=uname, display_name=display, role=role,
                password_hash=passwords.hash_password(password), token_epoch=0, failed_count=0,
                is_active=True, created_at=now))
            audit.append(conn, actor=actor, event="user.create", subject=uid,
                         detail={"username": uname, "role": role})
    except IntegrityError:
        raise Conflict("That username is taken.", code="username_taken") from None
    return {"id": uid, "username": uname, "role": role}


def list_users(ctx: Ctx) -> list[dict]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(select(users).order_by(users.c.username)).all()
    return [{"id": r.id, "username": r.username, "display_name": r.display_name, "role": r.role,
             "is_active": r.is_active, "last_login_at": iso(r.last_login_at),
             "locked": bool(r.locked_until)} for r in rows]


def set_active(ctx: Ctx, user_id: str, active: bool, actor: str, actor_id: str, now: datetime) -> dict:
    if user_id == actor_id and not active:
        raise Conflict("You cannot disable your own account.", code="self_disable")
    with ctx.engine.begin() as conn:
        res = conn.execute(update(users).where(users.c.id == user_id).values(
            is_active=active, token_epoch=users.c.token_epoch + 1, failed_count=0, locked_until=None))
        if res.rowcount == 0:
            raise NotFound("User not found.")
        audit.append(conn, actor=actor, event="user.enable" if active else "user.disable", subject=user_id)
    return {"is_active": active}


def reset_password(ctx: Ctx, user_id: str, new_password, actor: str, now: datetime) -> dict:
    with ctx.engine.begin() as conn:
        row = conn.execute(select(users).where(users.c.id == user_id)).first()
        if row is None:
            raise NotFound("User not found.")
        passwords.check_policy(new_password if isinstance(new_password, str) else "", row.username)
        conn.execute(update(users).where(users.c.id == user_id).values(
            password_hash=passwords.hash_password(new_password), token_epoch=users.c.token_epoch + 1,
            failed_count=0, locked_until=None))
        audit.append(conn, actor=actor, event="user.password_reset", subject=user_id)
    return {"reset": True}


def change_own_password(ctx: Ctx, user_id: str, current, new, now: datetime) -> dict:
    with ctx.engine.begin() as conn:
        row = conn.execute(select(users).where(users.c.id == user_id)).first()
        if row is None or not isinstance(current, str) or not passwords.verify_password(row.password_hash, current):
            raise Unauthorized("Current password is incorrect.", code="bad_credentials")
        passwords.check_policy(new if isinstance(new, str) else "", row.username)
        conn.execute(update(users).where(users.c.id == user_id).values(
            password_hash=passwords.hash_password(new), token_epoch=users.c.token_epoch + 1))
        audit.append(conn, actor=f"user:{row.username}", event="user.password_change", subject=user_id)
    return {"changed": True}


def login(ctx: Ctx, username, password, now: datetime) -> dict:
    uname = username.strip().lower()[:32] if isinstance(username, str) else ""
    pw = password if isinstance(password, str) and len(password) <= 256 else ""
    failure = None
    with ctx.engine.begin() as conn:
        row = conn.execute(select(users).where(users.c.username == uname)).first()
        stored = row.password_hash if row else passwords.DUMMY_HASH
        ok = passwords.verify_password(stored, pw)  # always runs: uniform timing

        if row is not None and row.locked_until and row.locked_until > now:
            raise TooManyRequests("Account temporarily locked. Try again later.", code="account_locked",
                                  retry_after=int((row.locked_until - now).total_seconds()) + 1)
        if row is None or not row.is_active or not ok:
            if row is not None:
                fails = row.failed_count + 1
                values = {"failed_count": fails}
                if fails >= MAX_FAILURES:
                    values["locked_until"] = now + LOCK_BASE * (2 ** min(fails - MAX_FAILURES, 4))
                conn.execute(update(users).where(users.c.id == row.id).values(**values))
                audit.append(conn, actor=f"user:{row.username}", event="login.failure", subject=row.id,
                             detail={"failed_count": fails})
            # Raised after the block so the failure counter commits instead of rolling back.
            failure = Unauthorized(GENERIC, code="bad_credentials")
        else:
            values = {"failed_count": 0, "locked_until": None, "last_login_at": now}
            if passwords.needs_rehash(row.password_hash):
                values["password_hash"] = passwords.hash_password(pw)
            conn.execute(update(users).where(users.c.id == row.id).values(**values))
            audit.append(conn, actor=f"user:{row.username}", event="login.success", subject=row.id)
            token, exp = ctx.sessions.mint(user_id=row.id, role=row.role, epoch=row.token_epoch,
                                           ttl=ctx.settings.session_ttl_s)
    if failure:
        raise failure
    return {"token": token, "expires_at": exp, "role": row.role,
            "display_name": row.display_name, "username": row.username}

