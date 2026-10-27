"""The Pi's enrolment endpoint. The signature is checked against the registered public key
BEFORE the body is parsed, and before any of its content is believed."""
from __future__ import annotations

import re
import time

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from .. import http
from ..db import devices
from ..errors import Forbidden, Unauthorized
from ..security.signing import canonical_request, verify_signature
from ..services import enrol as enrol_svc

bp = Blueprint("device", __name__)
_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")
_IDEM = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@bp.post("/api/v1/enrol")
def enrol():
    c = http.ctx()
    device_id = request.headers.get("X-Device-Id", "")
    ts = request.headers.get("X-Timestamp", "")
    idem = request.headers.get("X-Idempotency-Key", "")
    sig = request.headers.get("X-Signature", "")
    if not (_UUID.match(device_id) and _IDEM.match(idem) and ts.isdigit() and 0 < len(sig) <= 128):
        raise Unauthorized("Missing or malformed device headers.", code="bad_device_auth")

    http.limit(f"enrol-ip:{http.hashed_ip()}", 60, 60)  # bounds unauthenticated guessing
    with c.engine.connect() as conn:
        dev = conn.execute(select(devices).where(devices.c.device_id == device_id)).first()
    raw = request.get_data(cache=True)
    message = canonical_request(request.method, request.path, ts, idem, raw)
    # Unknown device and wrong signature are indistinguishable to the caller.
    if dev is None or not verify_signature(dev.public_key, sig, message):
        raise Unauthorized("Signature rejected.", code="bad_signature")
    if dev.status != "active":
        raise Forbidden("This device has been revoked.", code="device_revoked")
    if abs(time.time() - int(ts)) > c.settings.timestamp_window_s:
        raise Unauthorized("Timestamp outside the allowed window. Check the device clock.",
                           code="stale_timestamp")
    http.limit(f"enrol:{device_id}", 120, 60)

    status, body = enrol_svc.enrol(c, device_id=device_id, idem_key=idem, raw_body=raw, now=http.now())
    resp = jsonify(body)
    resp.status_code = status
    return resp
