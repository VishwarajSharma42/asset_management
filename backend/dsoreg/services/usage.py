"""A person reports how they used an asset. Overlapping sessions on one asset are normal
(a lab bench is shared), so there is deliberately no exclusion rule."""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import validators as v
from ..context import Ctx
from ..db import asset_state, fault_report, usage_log
from ..errors import BadRequest, Conflict, NotFound
from . import audit

_ALLOWED = {"ticket", "user_name", "start_at", "end_at", "working", "fault_note", "location", "location_error"}


def submit(ctx: Ctx, *, body: dict, ip_h: str, now: datetime) -> dict:
    if not isinstance(body, dict) or not set(body) <= _ALLOWED:
        raise BadRequest("Unrecognised fields.", code="bad_schema")
    claims = ctx.tickets.verify(body.get("ticket", ""))
    asset_id = claims["a"]

    name = v.person_name(body.get("user_name"))
    start, end = v.usage_window(body.get("start_at"), body.get("end_at"), now)
    working = body.get("working")
    if not isinstance(working, bool):
        raise BadRequest("working must be true or false.", code="invalid_field")
    note = v.fault_note(body.get("fault_note"), required=not working)
    if working:
        note = None
    # Checked BEFORE anything is written, so a refused location leaves the ticket unused and the
    # person can allow location access and press Submit again.
    place, place_status = v.location(body.get("location"), body.get("location_error"),
                                     required=ctx.settings.location_required)

    usage_id, fault_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        with ctx.engine.begin() as conn:
            state = conn.execute(select(asset_state.c.lifecycle, asset_state.c.version)
                                 .where(asset_state.c.asset_id == asset_id)).first()
            if state is None:
                raise NotFound("Asset not found.")
            if state.lifecycle != "active":
                raise Conflict("This asset has been retired.", code="asset_retired")
            conn.execute(usage_log.insert().values(
                id=usage_id, asset_id=asset_id, user_name=name, start_at=start, end_at=end,
                working=working, fault_note=note, submitted_at=now, ip_hash=ip_h,
                ticket_jti=claims["jti"], location_status=place_status,
                location_ct=(ctx.fields.encrypt("location", usage_id, json.dumps(place, separators=(",", ":")))
                             if place else None)))
            if not working:
                conn.execute(fault_report.insert().values(
                    id=fault_id, asset_id=asset_id, usage_id=usage_id, note=note,
                    reported_by=name, reported_at=now))
                conn.execute(update(asset_state).where(asset_state.c.asset_id == asset_id).values(
                    condition="faulty", version=asset_state.c.version + 1, updated_at=now))
                audit.append(conn, actor="public", event="fault.reported", subject=asset_id,
                             detail={"fault_id": fault_id, "usage_id": usage_id})
    except IntegrityError:
        raise Conflict("That scan was already used. Tap the tag again to submit another.",
                       code="ticket_used") from None
    return {"status": "recorded", "fault_flagged": not working, "location_recorded": place_status == "ok"}
