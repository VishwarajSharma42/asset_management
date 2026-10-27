"""The public tap: what the asset passport shows, and a one-time ticket for the usage form."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from ..context import Ctx
from ..db import asset_profile, asset_state, assets, usage_log
from ..security import taglink
from ..security.signing import verify_row
from ..security.util import consteq, hmac_index
from .common import row_core


def _row_ok(ctx: Ctx, row) -> bool:
    core = row_core(asset_id=row.id, token_hash=row.token_hash, product_index=row.product_index,
                    tag_uid_index=row.tag_uid_index, device_id=row.device_id, created_at=row.created_at)
    return verify_row(ctx.keys.row_signing_pub, core, row.row_sig)


def in_use_count(conn, asset_id: str, now: datetime) -> int:
    return conn.execute(select(func.count()).select_from(usage_log).where(
        usage_log.c.asset_id == asset_id, usage_log.c.start_at <= now, usage_log.c.end_at > now)).scalar()


def scan(ctx: Ctx, t, d, now: datetime) -> dict:
    """One answer shape for every unknown case, so the endpoint cannot be used to probe
    which tokens exist."""
    unregistered = {"verdict": "unregistered"}
    if not isinstance(t, str) or not isinstance(d, str) or len(t) > 64 or len(d) > 400:
        return unregistered
    try:
        token = taglink.parse_token(t)
    except taglink.TagDataError:
        return unregistered
    th = taglink.token_hash(token)

    with ctx.engine.connect() as conn:
        row = conn.execute(
            select(assets, asset_profile.c.name, asset_profile.c.room_no,
                   asset_profile.c.image_sha256, asset_state.c.lifecycle, asset_state.c.condition)
            .join(asset_profile, asset_profile.c.asset_id == assets.c.id)
            .join(asset_state, asset_state.c.asset_id == assets.c.id)
            .where(assets.c.token_hash == th)).first()
        if row is None:
            return unregistered
        if not _row_ok(ctx, row):
            return {"verdict": "integrity_error"}
        try:
            from_tag = taglink.open_product_id(ctx.settings.tag_key, token, d)
        except taglink.TagDataError:
            return {"verdict": "tag_mismatch"}
        if not consteq(hmac_index(ctx.settings.tag_index_key, "product", from_tag), row.product_index):
            return {"verdict": "tag_mismatch"}
        if row.lifecycle == "retired":
            return {"verdict": "retired", "asset": {"name": row.name}}
        busy = in_use_count(conn, row.id, now)

    ticket, expires = ctx.tickets.mint(row.id, ctx.settings.ticket_ttl_s)
    return {
        "verdict": "ok",
        "asset": {"name": row.name, "room_no": row.room_no, "condition": row.condition,
                  "image_url": f"/media/{row.image_sha256}.jpg" if row.image_sha256 else None,
                  "in_use_count": busy},
        "ticket": ticket,
        "ticket_expires": expires,
    }
