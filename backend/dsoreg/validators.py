"""Input validation. Duplicated in pi/dso_enroller/validators.py so the operator gets the
same answer on the Pi that the server will give. The server is the one that decides."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone

from .errors import Unprocessable

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
PRODUCT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.\-]{0,31}$")
ROOM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.\- ]{0,19}$")
PERSON_RE = re.compile(r"^[^\W_][\w .,'()\-]{1,79}$", re.UNICODE)
ASSET_NAME_RE = re.compile(r"^[^\W_][\w .,'()/&+\-]{1,99}$", re.UNICODE)
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._\-]{2,31}$")


def _clean(value, field: str, *, max_len: int) -> str:
    if not isinstance(value, str):
        raise Unprocessable(f"{field} must be text.", code="invalid_field", details={"field": field})
    value = unicodedata.normalize("NFC", value).strip()
    value = re.sub(r"\s+", " ", value)
    if _CONTROL.search(value) or len(value) > max_len:
        raise Unprocessable(f"{field} is not valid.", code="invalid_field", details={"field": field})
    return value


def _match(regex, value, field: str, hint: str) -> str:
    if not regex.match(value):
        raise Unprocessable(f"{field}: {hint}", code="invalid_field", details={"field": field})
    return value


def product_id(value) -> str:
    v = _clean(value, "product_id", max_len=32)
    return _match(PRODUCT_ID_RE, v, "product_id", "use letters, digits and / _ . - (max 32).")


def room_no(value) -> str:
    v = _clean(value, "room_no", max_len=20)
    return _match(ROOM_RE, v, "room_no", "use letters, digits and / _ . - (max 20).")


def asset_name(value) -> str:
    v = _clean(value, "name", max_len=100)
    return _match(ASSET_NAME_RE, v, "name", "2-100 characters, no special symbols.")


def person_name(value) -> str:
    v = _clean(value, "user_name", max_len=80)
    return _match(PERSON_RE, v, "user_name", "enter your name (letters, spaces . , ' - only).")


def username(value) -> str:
    v = _clean(value, "username", max_len=32).lower()
    return _match(USERNAME_RE, v, "username", "3-32 characters: a-z, 0-9, . _ -")


def fault_note(value, *, required: bool) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise Unprocessable("Describe what is wrong.", code="invalid_field",
                                details={"field": "fault_note"})
        return None
    v = _clean(value, "fault_note", max_len=500)
    if required and len(v) < 3:
        raise Unprocessable("Describe what is wrong.", code="invalid_field",
                            details={"field": "fault_note"})
    return v


def tag_uid(value) -> str:
    v = _clean(value, "tag_uid", max_len=20).upper()
    if not re.fullmatch(r"[0-9A-F]{8,20}", v):
        raise Unprocessable("tag_uid is not valid.", code="invalid_field", details={"field": "tag_uid"})
    return v


MAX_PAST = timedelta(days=7)
MAX_FUTURE = timedelta(hours=12)
MAX_SESSION = timedelta(hours=24)


def parse_time(value, field: str) -> datetime:
    if not isinstance(value, str):
        raise Unprocessable(f"{field} is required.", code="invalid_field", details={"field": field})
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Unprocessable(f"{field} is not a valid time.", code="invalid_field",
                            details={"field": field}) from None
    if dt.tzinfo is None:
        raise Unprocessable(f"{field} needs a timezone.", code="invalid_field", details={"field": field})
    return dt.astimezone(timezone.utc)


def usage_window(start_raw, end_raw, now: datetime) -> tuple[datetime, datetime]:
    start, end = parse_time(start_raw, "start_at"), parse_time(end_raw, "end_at")
    if end < start:
        raise Unprocessable("End time is before the start time.", code="invalid_window")
    if end - start > MAX_SESSION:
        raise Unprocessable("A single use cannot be longer than 24 hours.", code="invalid_window")
    if start < now - MAX_PAST or end > now + MAX_FUTURE:
        raise Unprocessable("Those times are outside the allowed range.", code="invalid_window")
    return start, end
