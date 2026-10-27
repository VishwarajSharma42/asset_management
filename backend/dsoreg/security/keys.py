"""Private keys at rest are wrapped under the KEK (AES-256-GCM) and unwrapped into memory
at start-up. Reading the environment alone does not give an attacker a usable key; they
also need the KEK, which is provisioned separately (see docs/DEPLOY.md).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import ConfigError, Settings

_RAW_PUB = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
_RAW_PRIV = dict(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
                 encryption_algorithm=serialization.NoEncryption())


def wrap(kek: bytes, name: str, secret: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(kek).encrypt(nonce, secret, f"dsoreg/wrap/{name}".encode())


def unwrap(kek: bytes, name: str, blob: bytes) -> bytes:
    try:
        return AESGCM(kek).decrypt(blob[:12], blob[12:], f"dsoreg/wrap/{name}".encode())
    except Exception as exc:
        raise ConfigError(f"could not unwrap {name}: wrong KEK or corrupted value") from exc


@dataclass(frozen=True)
class KeyRing:
    field_recipient_priv: X25519PrivateKey
    field_recipient_pub: bytes
    row_signing_priv: Ed25519PrivateKey
    row_signing_pub: Ed25519PublicKey
    session_signing_priv: Ed25519PrivateKey
    session_signing_pub: Ed25519PublicKey

    @classmethod
    def load(cls, s: Settings) -> "KeyRing":
        fr = X25519PrivateKey.from_private_bytes(
            unwrap(s.kek, "field_recipient", s.field_recipient_key_wrapped))
        row = Ed25519PrivateKey.from_private_bytes(
            unwrap(s.kek, "row_signing", s.row_signing_key_wrapped))
        sess = Ed25519PrivateKey.from_private_bytes(
            unwrap(s.kek, "session_signing", s.session_signing_key_wrapped))
        # The public halves in the environment must match the unwrapped privates, or a
        # misconfiguration would only show up as unverifiable data much later.
        if fr.public_key().public_bytes(**_RAW_PUB) != s.field_recipient_pub:
            raise ConfigError("FIELD_RECIPIENT_PUB does not match its private key")
        if row.public_key().public_bytes(**_RAW_PUB) != s.row_signing_pubkey:
            raise ConfigError("ROW_SIGNING_PUBKEY does not match its private key")
        if sess.public_key().public_bytes(**_RAW_PUB) != s.session_signing_pubkey:
            raise ConfigError("SESSION_SIGNING_PUBKEY does not match its private key")
        return cls(fr, s.field_recipient_pub, row, row.public_key(), sess, sess.public_key())
