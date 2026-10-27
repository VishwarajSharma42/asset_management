"""Staff session tokens (Ed25519) and one-time scan tickets (HMAC).

Session token = b64u(payload_json) + "." + b64u(signature). Short-lived, scoped, and
tied to the user's `epoch`, so changing a password or disabling an account invalidates
every token that user holds. The browser keeps it in memory only.

Scan ticket = proof that somebody *scanned this asset's tag* recently. The usage form
refuses to submit without one, and each ticket works exactly once.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from ..errors import Unauthorized
from .util import b64u, ub64u

ROLE_SCOPES = {
    "viewer": ["assets:read", "usage:read", "faults:read"],
    "admin": ["assets:read", "usage:read", "faults:read", "assets:write", "faults:write",
              "users:write", "audit:read"],
}


_SESSION_PREFIX = b"dsoreg/session/v1\n"


def _dump(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


class SessionTokens:
    def __init__(self, priv: Ed25519PrivateKey, pub: Ed25519PublicKey):
        self._priv, self._pub = priv, pub

    def mint(self, *, user_id: str, role: str, epoch: int, ttl: int, now: float | None = None) -> tuple[str, int]:
        now = int(now if now is not None else time.time())
        payload = {"v": 1, "sub": user_id, "role": role, "ep": epoch, "iat": now,
                   "exp": now + ttl, "jti": uuid.uuid4().hex}
        body = _dump(payload)
        sig = self._priv.sign(_SESSION_PREFIX + body)
        return f"{b64u(body)}.{b64u(sig)}", payload["exp"]

    def verify(self, token: str, now: float | None = None) -> dict:
        try:
            body_b64, sig_b64 = token.split(".", 1)
            body = ub64u(body_b64)
            self._pub.verify(ub64u(sig_b64), _SESSION_PREFIX + body)
            claims = json.loads(body)
        except (ValueError, InvalidSignature, KeyError, json.JSONDecodeError):
            raise Unauthorized("Invalid session.", code="invalid_token") from None
        if claims.get("v") != 1 or claims.get("exp", 0) < (now if now is not None else time.time()):
            raise Unauthorized("Session expired.", code="token_expired")
        return claims


class Tickets:
    def __init__(self, key: bytes):
        self._key = key

    def _mac(self, body: bytes) -> bytes:
        return hmac.new(self._key, b"dsoreg/ticket/v1\n" + body, hashlib.sha256).digest()

    def mint(self, asset_id: str, ttl: int, now: float | None = None) -> tuple[str, int]:
        now = int(now if now is not None else time.time())
        payload = {"a": asset_id, "exp": now + ttl, "jti": uuid.uuid4().hex}
        body = _dump(payload)
        return f"{b64u(body)}.{b64u(self._mac(body))}", payload["exp"]

    def verify(self, ticket: str, now: float | None = None) -> dict:
        try:
            body_b64, mac_b64 = ticket.split(".", 1)
            body = ub64u(body_b64)
            if not hmac.compare_digest(ub64u(mac_b64), self._mac(body)):
                raise ValueError
            claims = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            raise Unauthorized("Scan the tag again to continue.", code="invalid_ticket") from None
        if claims.get("exp", 0) < (now if now is not None else time.time()):
            raise Unauthorized("That scan has expired. Tap the tag again.", code="ticket_expired")
        return claims
