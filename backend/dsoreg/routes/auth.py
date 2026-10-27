from __future__ import annotations

from flask import Blueprint, g, jsonify

from .. import http
from ..security.tokens import ROLE_SCOPES
from ..services import users as users_svc

bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


@bp.post("/login")
def login():
    http.limit(f"login:{http.hashed_ip()}", 15, 300)
    body = http.json_body()
    return jsonify(users_svc.login(http.ctx(), body.get("username"), body.get("password"), http.now()))


@bp.get("/me")
@http.require("assets:read")
def me():
    return jsonify({**g.user, "scopes": ROLE_SCOPES[g.user["role"]]})


@bp.post("/password")
@http.require("assets:read")
def change_password():
    body = http.json_body()
    return jsonify(users_svc.change_own_password(http.ctx(), g.user["id"], body.get("current"),
                                                 body.get("new"), http.now()))
