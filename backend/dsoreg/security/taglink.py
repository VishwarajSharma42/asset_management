"""What is physically written on the tag, and how the product ID on it is protected.

    https://<host>/c?t=<token>&d=<sealed product id>

t  16 random bytes, base64url (22 chars). The register stores only SHA-256(token).
d  AES-128-GCM of the product ID under a key derived per tag:
       key   = HKDF-SHA256(TAG_KEY, salt=token, info="dsoreg/tag-d/v1") -> 16 bytes
       blob  = 0x01 | nonce(12) | ciphertext+tag
   The server opens `d` on every scan and compares it with the stored product ID, so a
   tag whose data was swapped or edited is reported as a mismatch.

Duplicated in pi/dso_enroller/crypto.py and pinned by tests/vectors/taglink.json.
"""
from __future__ import annotations

import os
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .util import b64u, sha256_hex, ub64u

VERSION = b"\x01"
AAD = b"dsoreg/tag-d/v1"
TOKEN_BYTES = 16


class TagDataError(ValueError):
    pass


def _key(tag_key: bytes, token: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=16, salt=token,
                info=b"dsoreg/tag-d/v1").derive(tag_key)


def seal_product_id(tag_key: bytes, token: bytes, product_id: str, *, _nonce: bytes | None = None) -> str:
    nonce = _nonce or os.urandom(12)
    ct = AESGCM(_key(tag_key, token)).encrypt(nonce, product_id.encode(), AAD)
    return b64u(VERSION + nonce + ct)


def open_product_id(tag_key: bytes, token: bytes, d: str) -> str:
    try:
        blob = ub64u(d)
        if blob[:1] != VERSION or len(blob) < 1 + 12 + 16:
            raise TagDataError("bad version")
        return AESGCM(_key(tag_key, token)).decrypt(blob[1:13], blob[13:], AAD).decode()
    except TagDataError:
        raise
    except Exception as exc:
        raise TagDataError("tag data did not open") from exc


def token_hash(token: bytes) -> str:
    return sha256_hex(token)


def parse_token(t: str) -> bytes:
    try:
        token = ub64u(t)
    except Exception as exc:
        raise TagDataError("bad token") from exc
    if len(token) != TOKEN_BYTES:
        raise TagDataError("bad token length")
    return token


def parse_link(link: str, *, host: str, allow_http: bool = False) -> tuple[bytes, str]:
    """Validate a tag link and return (token, d). The host must be ours."""
    parts = urlsplit(link)
    schemes = {"https", "http"} if allow_http else {"https"}
    if parts.scheme not in schemes or (parts.hostname or "").lower() != host.split(":")[0]:
        raise TagDataError("link host or scheme is not ours")
    if parts.path != "/c" or parts.fragment:
        raise TagDataError("link path is not /c")
    query = parse_qs(parts.query, strict_parsing=True)
    if set(query) != {"t", "d"} or any(len(v) != 1 for v in query.values()):
        raise TagDataError("link must carry exactly t and d")
    return parse_token(query["t"][0]), query["d"][0]
