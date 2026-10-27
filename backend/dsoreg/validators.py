"""Input validation. Duplicated in pi/dso_enroller/validators.py so the operator gets the
same answer on the Pi that the server will give. The server is the one that decides."""
from __future__ import annotations

import math
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


def _coordinate(value, field: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise Unprocessable(f"location {field} is not valid.", code="invalid_field", details={"field": f"location.{field}"})
    return float(value)


LOCATION_ERRORS = {"denied": "denied", "unavailable": "unavailable", "timeout": "unavailable"}


def location(value, error, *, required: bool) -> tuple[dict | None, str]:
    """(coordinates or None, status). Coordinates are what the person's phone CLAIMS: the
    server cannot verify them, so they are evidence of a claim, not of presence."""
    if value is not None and error is not None:
        raise Unprocessable("Send a location or the reason it is missing, not both.", code="invalid_field",
                            details={"field": "location"})
    if value is None:
        if required:
            raise Unprocessable("Location is required. Allow location access on your phone and try again.",
                                code="location_required")
        if error is None:
            return None, "none"
        if error not in LOCATION_ERRORS:
            raise Unprocessable("location_error is not valid.", code="invalid_field",
                                details={"field": "location_error"})
        return None, LOCATION_ERRORS[error]
    if not isinstance(value, dict) or not set(value) <= {"lat", "lng", "accuracy"} or \
            not {"lat", "lng"} <= set(value):
        raise Unprocessable("location must be {lat, lng, accuracy}.", code="invalid_field",
                            details={"field": "location"})
    lat = _coordinate(value["lat"], "lat", -90.0, 90.0)
    lng = _coordinate(value["lng"], "lng", -180.0, 180.0)
    acc = value.get("accuracy")
    acc = None if acc is None else round(_coordinate(acc, "accuracy", 0.0, 100_000.0), 1)
    return {"lat": round(lat, 6), "lng": round(lng, 6), "acc": acc}, "ok"
