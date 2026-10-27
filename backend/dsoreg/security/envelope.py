"""Sealed boxes: X25519 + HKDF-SHA256 + AES-128-GCM.

The Pi seals records TO the backend's public key and cannot open them again. Wire format:

    version(1) = 0x01 | ephemeral_pub(32) | nonce(12) | ciphertext+tag

Duplicated byte-for-byte in pi/dso_enroller/crypto.py and pinned by
backend/tests/vectors/envelope.json — the two trees never import each other.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = b"\x01"
INFO = b"dsoreg/seal/v1"
_RAW = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


class OpenError(ValueError):
    pass


def _key(shared: bytes, eph_pub: bytes, recipient_pub: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=16, salt=eph_pub + recipient_pub,
                info=INFO).derive(shared)


def seal(plaintext: bytes, recipient_pub: bytes, aad: bytes, *,
         _eph: X25519PrivateKey | None = None, _nonce: bytes | None = None) -> bytes:
    eph = _eph or X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(**_RAW)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(recipient_pub))
    nonce = _nonce or os.urandom(12)
    ct = AESGCM(_key(shared, eph_pub, recipient_pub)).encrypt(nonce, plaintext, VERSION + aad)
    return VERSION + eph_pub + nonce + ct


def open_sealed(blob: bytes, recipient_priv: X25519PrivateKey, aad: bytes) -> bytes:
    if len(blob) < 1 + 32 + 12 + 16 or blob[:1] != VERSION:
        raise OpenError("malformed sealed box")
    eph_pub, nonce, ct = blob[1:33], blob[33:45], blob[45:]
    recipient_pub = recipient_priv.public_key().public_bytes(**_RAW)
    try:
        shared = recipient_priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
        return AESGCM(_key(shared, eph_pub, recipient_pub)).decrypt(nonce, ct, VERSION + aad)
    except Exception as exc:  # InvalidTag, bad point, ...
        raise OpenError("sealed box did not open") from exc
