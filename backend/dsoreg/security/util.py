from __future__ import annotations

import base64
import hashlib
import hmac


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def ub64u(text: str) -> bytes:
    if not isinstance(text, str) or not text.isascii():
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hmac_index(key: bytes, label: str, value: str) -> str:
    """Keyed lookup index. Without `key` a database dump cannot be tested against guesses."""
    msg = label.encode("ascii") + b"\x00" + value.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def ip_hash(seed: bytes, ip: str, day: str) -> str:
    """Daily-rotating pseudonym for an address. The address itself is never stored."""
    return hmac.new(seed, f"{day}|{ip}".encode(), hashlib.sha256).hexdigest()[:24]


def consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
