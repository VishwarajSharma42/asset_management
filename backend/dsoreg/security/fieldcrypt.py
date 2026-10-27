"""Encryption of columns at rest: AES-128-GCM, one derived key per (column, row).

A stolen database dump holds ciphertext only. The KEK lives outside the database and
outside backups (systemd credential or a root-only file).

    key   = HKDF-SHA256(KEK, salt=row_id, info="dsoreg/field/v1|<column>")  -> 16 bytes
    blob  = nonce(12) | ciphertext+tag            AAD = "<column>|<row_id>"

The AAD binds a ciphertext to its own cell: copying a blob from one row, or one column,
to another fails authentication instead of silently decrypting.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class FieldCryptError(ValueError):
    pass


class FieldCrypto:
    def __init__(self, kek: bytes):
        if len(kek) != 32:
            raise FieldCryptError("KEK must be 32 bytes")
        self._kek = kek

    def _key(self, column: str, row_id: str) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=16, salt=row_id.encode(),
                    info=f"dsoreg/field/v1|{column}".encode()).derive(self._kek)

    def encrypt(self, column: str, row_id: str, plaintext: str) -> bytes:
        nonce = os.urandom(12)
        aad = f"{column}|{row_id}".encode()
        return nonce + AESGCM(self._key(column, row_id)).encrypt(nonce, plaintext.encode(), aad)

    def decrypt(self, column: str, row_id: str, blob: bytes) -> str:
        if len(blob) < 12 + 16:
            raise FieldCryptError("ciphertext too short")
        aad = f"{column}|{row_id}".encode()
        try:
            return AESGCM(self._key(column, row_id)).decrypt(blob[:12], blob[12:], aad).decode()
        except Exception as exc:
            raise FieldCryptError("field failed authentication") from exc
