"""Enrolment: the Pi registers one tag. Everything is checked before anything is written,
and the signed row, profile, state and audit entry land in ONE transaction."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import validators as v
from ..context import Ctx
from ..db import asset_profile, asset_state, assets, idempotency
from ..errors import BadRequest, Conflict, TooManyRequests, Unprocessable
from ..security import envelope, taglink
from ..security.signing import sign_row
from ..security.util import consteq, hmac_index, sha256_hex, ub64u
from . import audit, images
from .common import iso, row_core

SCHEMA = "dsoreg.enrol.v1"
_ALLOWED = {"schema", "name", "room_no", "token_hash", "sealed", "image", "lock_requested"}
_HASH = re.compile(r"^[0-9a-f]{64}$")


def enrol(ctx: Ctx, *, device_id: str, idem_key: str, raw_body: bytes, now: datetime) -> tuple[int, dict]:
    request_hash = sha256_hex(raw_body)

    with ctx.engine.connect() as conn:
        prior = conn.execute(select(idempotency).where(idempotency.c.key == idem_key)).first()
    if prior is not None:
        if prior.device_id != device_id or prior.request_hash != request_hash:
            raise Conflict("That idempotency key was used for a different request.",
                           code="idempotency_key_reuse")
        return prior.status, json.loads(prior.body)

    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        raise BadRequest("Body is not valid JSON.") from None
    if not isinstance(body, dict) or not set(body) <= _ALLOWED or body.get("schema") != SCHEMA:
        raise BadRequest("Unrecognised enrolment payload.", code="bad_schema")

    name = v.asset_name(body.get("name"))
    room = v.room_no(body.get("room_no"))
    token_hash = body.get("token_hash")
    if not isinstance(token_hash, str) or not _HASH.match(token_hash):
        raise Unprocessable("token_hash is not valid.", code="invalid_field")
    lock_requested = bool(body.get("lock_requested", False))

    try:
        sealed = ub64u(body["sealed"])
        plain = envelope.open_sealed(sealed, ctx.keys.field_recipient_priv,
                                     f"enrol|{device_id}|{token_hash}".encode())
        record = json.loads(plain)
    except (KeyError, ValueError, envelope.OpenError):
        raise Unprocessable("The sealed record could not be opened.", code="bad_seal") from None
    if not isinstance(record, dict) or set(record) != {"product_id", "tag_uid", "link"}:
        raise Unprocessable("The sealed record is malformed.", code="bad_seal")

    product_id = v.product_id(record["product_id"])
    uid = v.tag_uid(record["tag_uid"])
    link = record["link"]
    if not isinstance(link, str) or len(link) > 400:
        raise Unprocessable("The link is not valid.", code="bad_link")
    try:
        token, d = taglink.parse_link(link, host=ctx.settings.public_host,
                                      allow_http=ctx.settings.allow_http_links)
        if not consteq(taglink.token_hash(token), token_hash):
            raise taglink.TagDataError("token does not match token_hash")
        if taglink.open_product_id(ctx.settings.tag_key, token, d) != product_id:
            raise taglink.TagDataError("tag data does not match the product ID")
    except taglink.TagDataError as exc:
        raise Unprocessable(f"The tag link is not consistent: {exc}.", code="bad_link") from None

    image_hash = None
    if body.get("image"):
        if not isinstance(body["image"], str):
            raise Unprocessable("image must be base64 text.", code="invalid_image")
        image_hash = images.process_upload(body["image"], ctx.settings.media_dir)

    asset_id = str(uuid.uuid4())
    s = ctx.settings
    product_index = hmac_index(s.tag_index_key, "product", product_id)
    uid_index = hmac_index(s.tag_index_key, "uid", uid)
    core = row_core(asset_id=asset_id, token_hash=token_hash, product_index=product_index,
                    tag_uid_index=uid_index, device_id=device_id, created_at=now)
    response = {"status": "enrolled", "asset_id": asset_id, "created_at": iso(now)}

    try:
        with ctx.engine.begin() as conn:
            recent = conn.execute(select(func.count()).select_from(assets).where(
                assets.c.device_id == device_id, assets.c.created_at >= now - timedelta(hours=24))).scalar()
            if recent >= s.device_daily_enrol_limit:
                raise TooManyRequests("This device has reached its daily enrolment limit.",
                                      code="device_quota_exceeded", retry_after=3600)
            conn.execute(assets.insert().values(
                id=asset_id, product_index=product_index,
                product_id_ct=ctx.fields.encrypt("product_id", asset_id, product_id),
                tag_uid_index=uid_index, tag_uid_ct=ctx.fields.encrypt("tag_uid", asset_id, uid),
                token_hash=token_hash, link_ct=ctx.fields.encrypt("link", asset_id, link),
                device_id=device_id, lock_requested=lock_requested, created_at=now,
                row_sig=sign_row(ctx.keys.row_signing_priv, core), row_key_version=1))
            conn.execute(asset_profile.insert().values(
                asset_id=asset_id, name=name, room_no=room, image_sha256=image_hash,
                version=1, updated_at=now, updated_by=f"device:{device_id[:8]}"))
            conn.execute(asset_state.insert().values(
                asset_id=asset_id, lifecycle="active", condition="working", version=1, updated_at=now))
            audit.append(conn, actor=f"device:{device_id}", event="asset.enrol", subject=asset_id,
                         detail={"name": name, "room_no": room, "lock_requested": lock_requested,
                                 "has_image": bool(image_hash)})
            conn.execute(idempotency.insert().values(
                key=idem_key, device_id=device_id, request_hash=request_hash, status=201,
                body=json.dumps(response), created_at=now))
    except IntegrityError:
        # Product ID, tag UID or token already registered. One message for all three:
        # the caller does not learn which identifier collided.
        raise Conflict("This product or tag is already registered.", code="already_enrolled") from None
    return 201, response
