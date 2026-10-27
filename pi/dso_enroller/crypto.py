"""Cryptography used by the Pi. Byte-for-byte compatible with backend/dsoreg/security/*,
which is pinned by backend/tests/vectors/ (the two trees never import each other).

  * seal()               X25519 + HKDF-SHA256 + AES-128-GCM sealed box  (backend: envelope.py)
  * seal_product_id()    AES-128-GCM of the product ID written on the tag (backend: taglink.py)
  * sign_request()       Ed25519 over a canonical request string      (backend: signing.py)

The Pi holds a signing key and a PUBLIC encryption key. It can write records; it cannot
read the register back.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_RAW = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- sealed box ----------------------------------------------------------------------

SEAL_VERSION = b"\x01"


def seal(plaintext: bytes, recipient_pub: bytes, aad: bytes, *, _eph=None, _nonce=None) -> bytes:
    eph = _eph or X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(**_RAW)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(recipient_pub))
    key = HKDF(algorithm=hashes.SHA256(), length=16, salt=eph_pub + recipient_pub,
               info=b"dsoreg/seal/v1").derive(shared)
    nonce = _nonce or os.urandom(12)
    return SEAL_VERSION + eph_pub + nonce + AESGCM(key).encrypt(nonce, plaintext, SEAL_VERSION + aad)


# --- the product ID written on the tag ------------------------------------------------

TAG_VERSION = b"\x01"
TAG_AAD = b"dsoreg/tag-d/v1"


def seal_product_id(tag_key: bytes, token: bytes, product_id: str, *, _nonce=None) -> str:
    key = HKDF(algorithm=hashes.SHA256(), length=16, salt=token, info=b"dsoreg/tag-d/v1").derive(tag_key)
    nonce = _nonce or os.urandom(12)
    return b64u(TAG_VERSION + nonce + AESGCM(key).encrypt(nonce, product_id.encode(), TAG_AAD))


# --- request signing -------------------------------------------------------------------

def canonical_request(method: str, path: str, timestamp: str, idem_key: str, body: bytes) -> bytes:
    return "\n".join(["DSO1", method.upper(), path, timestamp, idem_key, sha256_hex(body)]).encode()


def sign_request(key: Ed25519PrivateKey, method: str, path: str, timestamp: str, idem_key: str,
                 body: bytes) -> str:
    return key.sign(canonical_request(method, path, timestamp, idem_key, body)).hex()


def load_signing_key(hex_private: str) -> Ed25519PrivateKey:
    raw = bytes.fromhex(hex_private.strip())
    if len(raw) != 32:
        raise ValueError("device key must be 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)
