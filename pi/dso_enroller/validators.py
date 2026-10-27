"""The same input rules the server applies, so the operator learns about a bad value at the
keyboard and not from a rejected upload. The server remains the authority."""
from __future__ import annotations

import re
import unicodedata


class ValidationError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
PRODUCT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.\-]{0,31}$")
ROOM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.\- ]{0,19}$")
ASSET_NAME_RE = re.compile(r"^[^\W_][\w .,'()/&+\-]{1,99}$", re.UNICODE)


def _clean(value: str, field: str, max_len: int) -> str:
    value = unicodedata.normalize("NFC", value or "").strip()
    value = re.sub(r"\s+", " ", value)
    if _CONTROL.search(value) or len(value) > max_len:
        raise ValidationError(field, "contains characters that are not allowed")
    return value


def product_id(value: str) -> str:
    v = _clean(value, "product_id", 32)
    if not PRODUCT_ID_RE.match(v):
        raise ValidationError("product_id", "use letters, digits and / _ . - (max 32 characters)")
    return v


def room_no(value: str) -> str:
    v = _clean(value, "room_no", 20)
    if not ROOM_RE.match(v):
        raise ValidationError("room_no", "use letters, digits and / _ . - (max 20 characters)")
    return v


def asset_name(value: str) -> str:
    v = _clean(value, "name", 100)
    if not ASSET_NAME_RE.match(v):
        raise ValidationError("name", "2-100 characters, no special symbols")
    return v
