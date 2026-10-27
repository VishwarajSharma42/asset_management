"""The console API. Routes validate and serialise; services decide."""
from __future__ import annotations

from flask import Blueprint, Response, g, jsonify, request
from sqlalchemy import select

from .. import http
from ..db import audit_log
from ..errors import BadRequest, PreconditionRequired
from ..services import audit as audit_svc
from ..services import registry
from ..services import users as users_svc

bp = Blueprint("staff", __name__, url_prefix="/api/v1")


def _if_match() -> int:
    raw = request.headers.get("If-Match", "").strip().strip('"')
    if not raw:
        raise PreconditionRequired()
    if not raw.isdigit():
        raise BadRequest("If-Match must be the version number.", code="bad_if_match")
    return int(raw)


@bp.get("/overview")
@http.require("assets:read")
def overview():
    return jsonify(registry.overview(http.ctx(), http.now()))


@bp.get("/assets")
@http.require("assets:read")
def assets():
    return jsonify(registry.list_assets(http.ctx(), request.args.to_dict(), http.now()))


@bp.get("/assets/<asset_id>")
@http.require("assets:read")
def asset(asset_id: str):
    data = registry.get_asset(http.ctx(), asset_id, http.now(), secrets=g.user["role"] == "admin")
    resp = jsonify(data)
    resp.headers["ETag"] = f'"{data["profile_version"]}"'
    return resp


@bp.patch("/assets/<asset_id>")
@http.require("assets:write")
def update_asset(asset_id: str):
    return jsonify(registry.update_profile(http.ctx(), asset_id, http.json_body(3 * 1024 * 1024), _if_match(),
                                           http.actor(), http.now()))


@bp.post("/assets/<asset_id>/lifecycle")
@http.require("assets:write")
def lifecycle(asset_id: str):
    body = http.json_body()
    return jsonify(registry.set_lifecycle(http.ctx(), asset_id, body.get("lifecycle"),
                                          body.get("reason"), http.actor(), http.now()))


@bp.get("/assets/<asset_id>/usage")
@http.require("usage:read")
def asset_usage(asset_id: str):
    return jsonify(registry.asset_usage(http.ctx(), asset_id, request.args.to_dict(), http.now()))


@bp.get("/faults")
@http.require("faults:read")
def faults():
    status = request.args.get("status", "open")
    return jsonify({"items": registry.list_faults(http.ctx(), status)})


@bp.post("/faults/<fault_id>/resolve")
@http.require("faults:write")
def resolve_fault(fault_id: str):
    return jsonify(registry.resolve_fault(http.ctx(), fault_id, http.json_body().get("note"),
                                          http.actor(), http.now()))


@bp.get("/export/usage.csv")
@http.require("usage:read")
def export_usage():
    data = registry.export_usage_csv(http.ctx(), request.args.to_dict(), http.now())
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=usage.csv"})


@bp.get("/users")
@http.require("users:write")
def list_users():
    return jsonify({"items": users_svc.list_users(http.ctx())})


@bp.post("/users")
@http.require("users:write")
def create_user():
    b = http.json_body()
    result = users_svc.create_user(http.ctx(), username=b.get("username"), display_name=b.get("display_name"),
                                   role=b.get("role"), password=b.get("password"), actor=http.actor(),
                                   now=http.now())
    return jsonify(result), 201


@bp.post("/users/<user_id>/active")
@http.require("users:write")
def user_active(user_id: str):
    active = http.json_body().get("active")
    if not isinstance(active, bool):
        raise BadRequest("active must be true or false.", code="invalid_field")
    return jsonify(users_svc.set_active(http.ctx(), user_id, active, http.actor(), g.user["id"], http.now()))


@bp.post("/users/<user_id>/password")
@http.require("users:write")
def user_password(user_id: str):
    return jsonify(users_svc.reset_password(http.ctx(), user_id, http.json_body().get("password"),
                                            http.actor(), http.now()))


@bp.get("/audit")
@http.require("audit:read")
def audit():
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
        before = int(request.args["before"]) if "before" in request.args else None
    except ValueError:
        raise BadRequest("limit and before must be numbers.") from None
    with http.ctx().engine.connect() as conn:
        return jsonify({"items": audit_svc.page(conn, limit=limit, before_seq=before)})


@bp.get("/audit/verify")
@http.require("audit:read")
def audit_verify():
    with http.ctx().engine.connect() as conn:
        return jsonify(audit_svc.verify(conn))
