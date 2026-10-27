"""Staff-facing reads and writes on assets. Every mutation checks a version (optimistic
concurrency) and writes its audit row in the same transaction."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, or_, select, update

from .. import validators as v
from ..context import Ctx
from ..db import asset_profile, asset_state, assets, fault_report, usage_log
from ..errors import BadRequest, Conflict, NotFound
from ..security.fieldcrypt import FieldCryptError
from ..security.util import hmac_index
from . import audit, images
from .common import escape_like, iso
from .scan import _row_ok

SORTS = {"name": asset_profile.c.name, "room": asset_profile.c.room_no,
         "created": assets.c.created_at, "condition": asset_state.c.condition}


def _decrypt(ctx: Ctx, column: str, row) -> str | None:
    try:
        return ctx.fields.decrypt(column, row.id, getattr(row, f"{column}_ct"))
    except FieldCryptError:
        return None


def _asset_dict(ctx: Ctx, row, *, secrets: bool = False) -> dict:
    d = {
        "id": row.id,
        "product_id": _decrypt(ctx, "product_id", row),
        "name": row.name, "room_no": row.room_no,
        "image_url": f"/media/{row.image_sha256}.jpg" if row.image_sha256 else None,
        "condition": row.condition, "lifecycle": row.lifecycle,
        "created_at": iso(row.created_at),
        "profile_version": row.profile_version,
        "integrity": _row_ok(ctx, row),
        "in_use_count": getattr(row, "in_use_count", 0) or 0,
        "last_used_at": iso(getattr(row, "last_used_at", None)),
        "open_faults": getattr(row, "open_faults", 0) or 0,
    }
    if secrets:
        d["tag_uid"] = _decrypt(ctx, "tag_uid", row)
        d["tag_link"] = _decrypt(ctx, "link", row)
        d["lock_requested"] = row.lock_requested
    return d


def _base_query(now: datetime):
    in_use = (select(usage_log.c.asset_id, func.count().label("in_use_count"))
              .where(usage_log.c.start_at <= now, usage_log.c.end_at > now)
              .group_by(usage_log.c.asset_id).subquery())
    last = (select(usage_log.c.asset_id, func.max(usage_log.c.start_at).label("last_used_at"))
            .where(usage_log.c.start_at <= now).group_by(usage_log.c.asset_id).subquery())
    faults = (select(fault_report.c.asset_id, func.count().label("open_faults"))
              .where(fault_report.c.resolved_at.is_(None)).group_by(fault_report.c.asset_id).subquery())
    q = (select(assets, asset_profile.c.name, asset_profile.c.room_no, asset_profile.c.image_sha256,
                asset_profile.c.version.label("profile_version"),
                asset_state.c.lifecycle, asset_state.c.condition,
                in_use.c.in_use_count, last.c.last_used_at, faults.c.open_faults)
         .join(asset_profile, asset_profile.c.asset_id == assets.c.id)
         .join(asset_state, asset_state.c.asset_id == assets.c.id)
         .outerjoin(in_use, in_use.c.asset_id == assets.c.id)
         .outerjoin(last, last.c.asset_id == assets.c.id)
         .outerjoin(faults, faults.c.asset_id == assets.c.id))
    return q, last, in_use


def list_assets(ctx: Ctx, args: dict, now: datetime) -> dict:
    q, last, in_use = _base_query(now)
    conds = []
    text = (args.get("q") or "").strip()[:60]
    if text:
        like = f"%{escape_like(text)}%"
        conds.append(or_(asset_profile.c.name.ilike(like, escape="\\"),
                         asset_profile.c.room_no.ilike(like, escape="\\"),
                         assets.c.product_index == hmac_index(ctx.settings.tag_index_key, "product", text)))
    if args.get("room"):
        conds.append(asset_profile.c.room_no == args["room"][:20])
    if args.get("condition") in {"working", "faulty"}:
        conds.append(asset_state.c.condition == args["condition"])
    if args.get("in_use") == "1":
        conds.append(in_use.c.in_use_count > 0)
    lifecycle = args.get("lifecycle", "active")
    if lifecycle in {"active", "retired"}:
        conds.append(asset_state.c.lifecycle == lifecycle)
    if conds:
        q = q.where(and_(*conds))

    sort_key = args.get("sort", "name")
    desc = sort_key.startswith("-")
    sort_key = sort_key.lstrip("-")
    if sort_key == "last_used":
        order = last.c.last_used_at
    elif sort_key in SORTS:
        order = SORTS[sort_key]
    else:
        raise BadRequest("Unknown sort.", code="invalid_sort")
    q = q.order_by(order.desc().nullslast() if desc else order.asc().nullsfirst(), assets.c.id)

    try:
        limit = max(1, min(int(args.get("limit", 50)), 200))
        offset = max(0, min(int(args.get("offset", 0)), 100000))
    except ValueError:
        raise BadRequest("limit and offset must be numbers.") from None

    with ctx.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(q.order_by(None).subquery())).scalar()
        rows = conn.execute(q.limit(limit).offset(offset)).all()
    return {"total": total, "limit": limit, "offset": offset,
            "items": [_asset_dict(ctx, r) for r in rows]}


def get_asset(ctx: Ctx, asset_id: str, now: datetime, *, secrets: bool) -> dict:
    q, _, _ = _base_query(now)
    with ctx.engine.connect() as conn:
        row = conn.execute(q.where(assets.c.id == asset_id)).first()
    if row is None:
        raise NotFound("Asset not found.")
    return _asset_dict(ctx, row, secrets=secrets)


def update_profile(ctx: Ctx, asset_id: str, body: dict, if_match: int, actor: str, now: datetime) -> dict:
    if not isinstance(body, dict) or not set(body) <= {"name", "room_no", "image"} or not body:
        raise BadRequest("Send name, room_no and/or image.", code="bad_schema")
    changes = {}
    if "name" in body:
        changes["name"] = v.asset_name(body["name"])
    if "room_no" in body:
        changes["room_no"] = v.room_no(body["room_no"])
    if body.get("image"):
        changes["image_sha256"] = images.process_upload(body["image"], ctx.settings.media_dir)

    with ctx.engine.begin() as conn:
        cur = conn.execute(select(asset_profile).where(asset_profile.c.asset_id == asset_id)).first()
        if cur is None:
            raise NotFound("Asset not found.")
        if cur.version != if_match:
            raise Conflict("Someone else changed this asset. Reload and try again.",
                           code="version_conflict", details={"current_version": cur.version})
        diff = {k: [getattr(cur, k), val] for k, val in changes.items() if getattr(cur, k) != val}
        if not diff:
            return {"version": cur.version, "changed": False}
        conn.execute(update(asset_profile).where(asset_profile.c.asset_id == asset_id).values(
            **changes, version=cur.version + 1, updated_at=now, updated_by=actor))
        audit.append(conn, actor=actor, event="asset.update", subject=asset_id,
                     detail={k: ["<image>", "<image>"] if k == "image_sha256" else val
                             for k, val in diff.items()})
    return {"version": cur.version + 1, "changed": True}


def set_lifecycle(ctx: Ctx, asset_id: str, lifecycle: str, reason, actor: str, now: datetime) -> dict:
    if lifecycle not in {"active", "retired"}:
        raise BadRequest("lifecycle must be active or retired.", code="invalid_field")
    reason = v.fault_note(reason, required=True)
    with ctx.engine.begin() as conn:
        res = conn.execute(update(asset_state).where(asset_state.c.asset_id == asset_id).values(
            lifecycle=lifecycle, version=asset_state.c.version + 1, updated_at=now))
        if res.rowcount == 0:
            raise NotFound("Asset not found.")
        audit.append(conn, actor=actor, event=f"asset.{lifecycle}", subject=asset_id,
                     detail={"reason": reason})
    return {"lifecycle": lifecycle}


def _place(ctx: Ctx, row) -> dict | None:
    if not row.location_ct:
        return None
    try:
        raw = json.loads(ctx.fields.decrypt("location", row.id, row.location_ct))
    except (FieldCryptError, ValueError):
        return None
    return {"lat": raw["lat"], "lng": raw["lng"], "accuracy": raw.get("acc")}


def asset_usage(ctx: Ctx, asset_id: str, args: dict, now: datetime, *, include_location: bool = False) -> dict:
    """Sessions for one asset, each tagged with how many other sessions overlapped it."""
    try:
        days = max(1, min(int(args.get("days", 14)), 365))
    except ValueError:
        raise BadRequest("days must be a number.") from None
    since = now - timedelta(days=days)
    with ctx.engine.connect() as conn:
        if conn.execute(select(assets.c.id).where(assets.c.id == asset_id)).first() is None:
            raise NotFound("Asset not found.")
        rows = conn.execute(select(usage_log).where(
            usage_log.c.asset_id == asset_id, usage_log.c.end_at >= since)
            .order_by(usage_log.c.start_at.desc()).limit(1000)).all()
    items = []
    for r in rows:
        overlaps = sum(1 for o in rows if o.id != r.id and o.start_at < r.end_at and o.end_at > r.start_at)
        items.append({"id": r.id, "user_name": r.user_name, "start_at": iso(r.start_at),
                      "end_at": iso(r.end_at), "working": r.working, "fault_note": r.fault_note,
                      "overlaps": overlaps, "active": r.start_at <= now < r.end_at,
                      "location_status": r.location_status,
                      **({"location": _place(ctx, r)} if include_location else {})})
    return {"since": iso(since), "items": items}


def overview(ctx: Ctx, now: datetime) -> dict:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with ctx.engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(asset_state).where(
            asset_state.c.lifecycle == "active")).scalar()
        faulty = conn.execute(select(func.count()).select_from(asset_state).where(
            asset_state.c.lifecycle == "active", asset_state.c.condition == "faulty")).scalar()
        busy = conn.execute(select(func.count(func.distinct(usage_log.c.asset_id))).where(
            usage_log.c.start_at <= now, usage_log.c.end_at > now)).scalar()
        sessions_today = conn.execute(select(func.count()).select_from(usage_log).where(
            usage_log.c.start_at >= day_start)).scalar()
        open_faults = conn.execute(select(func.count()).select_from(fault_report).where(
            fault_report.c.resolved_at.is_(None))).scalar()
        rooms = conn.execute(select(asset_profile.c.room_no, func.count()).join(
            asset_state, asset_state.c.asset_id == asset_profile.c.asset_id).where(
            asset_state.c.lifecycle == "active").group_by(asset_profile.c.room_no)
            .order_by(asset_profile.c.room_no)).all()
    return {"total_assets": total, "faulty": faulty, "in_use_now": busy,
            "sessions_today": sessions_today, "open_faults": open_faults,
            "rooms": [{"room_no": r[0], "count": r[1]} for r in rooms]}


def list_faults(ctx: Ctx, status: str) -> list[dict]:
    q = (select(fault_report, asset_profile.c.name, asset_profile.c.room_no)
         .join(asset_profile, asset_profile.c.asset_id == fault_report.c.asset_id)
         .order_by(fault_report.c.reported_at.desc()).limit(500))
    if status == "open":
        q = q.where(fault_report.c.resolved_at.is_(None))
    elif status == "resolved":
        q = q.where(fault_report.c.resolved_at.is_not(None))
    with ctx.engine.connect() as conn:
        rows = conn.execute(q).all()
    return [{"id": r.id, "asset_id": r.asset_id, "asset_name": r.name, "room_no": r.room_no,
             "note": r.note, "reported_by": r.reported_by, "reported_at": iso(r.reported_at),
             "resolved_at": iso(r.resolved_at), "resolved_by": r.resolved_by,
             "resolution_note": r.resolution_note} for r in rows]


def resolve_fault(ctx: Ctx, fault_id: str, note, actor: str, now: datetime) -> dict:
    note = v.fault_note(note, required=True)
    with ctx.engine.begin() as conn:
        f = conn.execute(select(fault_report).where(fault_report.c.id == fault_id)).first()
        if f is None:
            raise NotFound("Fault not found.")
        if f.resolved_at is not None:
            raise Conflict("That fault is already resolved.", code="already_resolved")
        conn.execute(update(fault_report).where(fault_report.c.id == fault_id).values(
            resolved_at=now, resolved_by=actor, resolution_note=note))
        still_open = conn.execute(select(func.count()).select_from(fault_report).where(
            fault_report.c.asset_id == f.asset_id, fault_report.c.resolved_at.is_(None))).scalar()
        if still_open == 0:
            conn.execute(update(asset_state).where(asset_state.c.asset_id == f.asset_id).values(
                condition="working", version=asset_state.c.version + 1, updated_at=now))
        audit.append(conn, actor=actor, event="fault.resolve", subject=f.asset_id,
                     detail={"fault_id": fault_id, "note": note})
    return {"resolved": True, "asset_condition": "working" if still_open == 0 else "faulty"}


def _safe_cell(value) -> str:
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_usage_csv(ctx: Ctx, args: dict, now: datetime, *, include_location: bool = False) -> str:
    try:
        days = max(1, min(int(args.get("days", 30)), 366))
    except ValueError:
        raise BadRequest("days must be a number.") from None
    q = (select(usage_log, asset_profile.c.name, asset_profile.c.room_no)
         .join(asset_profile, asset_profile.c.asset_id == usage_log.c.asset_id)
         .where(usage_log.c.start_at >= now - timedelta(days=days))
         .order_by(usage_log.c.start_at.desc()).limit(50000))
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["asset", "room", "user", "start_utc", "end_utc", "working", "fault_note", "location_status"]
               + (["lat", "lng", "accuracy_m"] if include_location else []))
    with ctx.engine.connect() as conn:
        for r in conn.execute(q):
            extra = []
            if include_location:
                p = _place(ctx, r)
                extra = [p["lat"], p["lng"], p["accuracy"]] if p else ["", "", ""]
            w.writerow([_safe_cell(x) for x in (r.name, r.room_no, r.user_name, iso(r.start_at), iso(r.end_at),
                                                 "yes" if r.working else "no", r.fault_note,
                                                 r.location_status, *extra)])
    return out.getvalue()
