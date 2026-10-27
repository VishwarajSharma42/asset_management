from __future__ import annotations

from datetime import datetime, timezone


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def row_core(*, asset_id: str, token_hash: str, product_index: str, tag_uid_index: str,
             device_id: str, created_at: datetime) -> dict:
    """The immutable part of an asset. This, and only this, is signed."""
    return {"v": 1, "id": asset_id, "token_hash": token_hash, "product_index": product_index,
            "tag_uid_index": tag_uid_index, "device_id": device_id, "created_at": iso(created_at)}


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
