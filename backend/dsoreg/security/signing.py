"""Device request signatures (Ed25519) and signed asset rows.

Request: the Pi signs a canonical string that binds the method, the exact path, a
timestamp, an idempotency key and the SHA-256 of the raw body, so none of them can be
swapped independently:

    DSO1\\n<METHOD>\\n<path>\\n<unix timestamp>\\n<idempotency key>\\n<sha256(body) hex>

Verification runs BEFORE the body is parsed.
"""
from __future__ import annotations

import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .util import sha256_hex


def canonical_request(method: str, path: str, timestamp: str, idem_key: str, body: bytes) -> bytes:
    return "\n".join(["DSO1", method.upper(), path, timestamp, idem_key, sha256_hex(body)]).encode()


def verify_signature(pub_hex: str, signature_hex: str, message: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex)).verify(
            bytes.fromhex(signature_hex), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def row_message(core: dict) -> bytes:
    return b"dsoreg/row/v1\n" + canonical_json(core)


def sign_row(priv: Ed25519PrivateKey, core: dict) -> str:
    return priv.sign(row_message(core)).hex()


def verify_row(pub: Ed25519PublicKey, core: dict, sig_hex: str) -> bool:
    try:
        pub.verify(bytes.fromhex(sig_hex), row_message(core))
        return True
    except (InvalidSignature, ValueError):
        return False
