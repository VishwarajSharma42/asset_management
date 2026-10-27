"""Anonymous surface: health, the tap, the usage form, and image files."""
from __future__ import annotations

import re

from flask import Blueprint, abort, jsonify, send_from_directory
from sqlalchemy import text

from .. import http
from ..errors import NotFound
from ..services import scan as scan_svc
from ..services import usage as usage_svc
from ..services.common import iso

bp = Blueprint("public", __name__)
_MEDIA = re.compile(r"^[0-9a-f]{64}\.jpg$")


@bp.get("/health")
def health():
    c = http.ctx()
    with c.engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return jsonify({"status": "ok", "database": "ok", "server_time": iso(http.now())})


@bp.post("/api/v1/scan")
def scan():
    http.require_campus_network()
    http.limit(f"scan:{http.hashed_ip()}", 40, 60)
    body = http.json_body()
    return jsonify(scan_svc.scan(http.ctx(), body.get("t"), body.get("d"), http.now()))


@bp.post("/api/v1/usage")
def usage():
    http.require_campus_network()
    http.limit(f"usage:{http.hashed_ip()}", 10, 60)
    result = usage_svc.submit(http.ctx(), body=http.json_body(), ip_h=http.hashed_ip(), now=http.now())
    return jsonify(result), 201


@bp.get("/media/<name>")
def media(name: str):
    if not _MEDIA.match(name):
        raise NotFound()
    folder = http.ctx().settings.media_dir
    resp = send_from_directory(folder, name, mimetype="image/jpeg", max_age=31536000)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp
