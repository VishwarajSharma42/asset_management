"""What gets written to the tag: one NDEF URI record.

    https://<host>/c?t=<token>&d=<AES-128-GCM sealed product id>

Pure arithmetic, no hardware. Every length rule here exists because a tag that cannot be
read back in one PN532 frame cannot be verified, and an unverified write is exactly what
the read-back compare is there to prevent.
"""
from __future__ import annotations

from .crypto import b64u

NDEF_START_PAGE = 0x04
PN532_FRAME_BYTES = 252          # most one FAST_READ returns in a single frame
MAX_NDEF_BYTES = PN532_FRAME_BYTES
NTAG216_LAST_USER_PAGE = 0xE1
TOKEN_BYTES = 16
URI_ABBREV = {"https://": 0x04, "http://": 0x03}


class LayoutError(ValueError):
    pass


def build_link(host: str, token: bytes, d: str, *, scheme: str = "https") -> str:
    if len(token) != TOKEN_BYTES:
        raise LayoutError("token must be 16 bytes")
    if not host or "/" in host or "@" in host or " " in host:
        raise LayoutError(f"host must be a bare hostname, got {host!r}")
    if scheme not in ("https", "http"):
        raise LayoutError("scheme must be https or http")
    return f"{scheme}://{host}/c?t={b64u(token)}&d={d}"


def build_ndef_tlv(url: str) -> bytes:
    """Type-2-Tag NDEF URI record, TLV-wrapped, padded to whole 4-byte pages."""
    prefix = next((p for p in URI_ABBREV if url.startswith(p)), None)
    if prefix is None:
        raise LayoutError("URL must be http(s)")
    payload = bytes([URI_ABBREV[prefix]]) + url[len(prefix):].encode("ascii")
    if len(payload) > 254 - 4:
        raise LayoutError(f"payload {len(payload)}B too long for a short NDEF record")
    record = bytes([0xD1, 0x01, len(payload), 0x55]) + payload
    tlv = bytes([0x03, len(record)]) + record + bytes([0xFE])
    if len(tlv) > MAX_NDEF_BYTES:
        raise LayoutError(f"NDEF is {len(tlv)}B; the limit is {MAX_NDEF_BYTES}B — use a shorter host")
    return tlv + b"\x00" * ((4 - len(tlv) % 4) % 4)


def pages_for(tlv: bytes) -> int:
    return len(tlv) // 4


def decode_url(tlv: bytes) -> str:
    """Inverse of build_ndef_tlv(); used by the simulator and the read-back check."""
    if tlv[0] != 0x03:
        raise LayoutError("not an NDEF TLV")
    record = tlv[2:2 + tlv[1]]
    prefix = {v: k for k, v in URI_ABBREV.items()}[record[4]]
    return prefix + record[5:].decode("ascii")
