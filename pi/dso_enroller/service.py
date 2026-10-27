"""Enrolment orchestration. The GUI calls this; so do the tests. No UI code in here.

Order matters and each ordering has a reason:

  validate -> present -> identify -> write -> verify -> QUEUE -> lock
                                                        |         |
        the record is durable BEFORE the irreversible step         |
        a failed lock leaves a registered, working, merely-unlocked tag
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable

from . import crypto, images, layout, ntag, validators
from .reader import Reader, ReaderError

log = logging.getLogger("dso.service")
STEPS = ["validate", "present", "identify", "write", "verify", "queue", "lock"]
Progress = Callable[[str, str, str], None]      # (step, "start"|"done"|"skip"|"fail", detail)


@dataclass
class EnrolForm:
    name: str
    product_id: str
    room_no: str
    image_path: str | None = None
    lock: bool = False


@dataclass
class EnrolResult:
    outbox_id: int
    uid: str
    link: str
    pages: int
    locked: bool
    lock_error: str | None = None


class EnrolError(Exception):
    def __init__(self, step: str, message: str, technical: str = ""):
        super().__init__(message)
        self.step, self.message, self.technical = step, message, technical


def _friendly(exc: Exception) -> str:
    if isinstance(exc, ntag.TagRejected):
        return "This is not an NTAG216 tag. Use a different tag."
    if isinstance(exc, ntag.WriteFailed):
        return (f"The tag stopped answering while writing (page {exc.page:#04x}). Hold it flat and still "
                f"on the reader and try again. Details: {exc.cause}")
    if isinstance(exc, ReaderError):
        return str(exc)
    return str(exc)


class EnrolService:
    def __init__(self, cfg, reader: Reader, outbox, on_queued: Callable[[], None] | None = None):
        self.cfg, self.reader, self.outbox = cfg, reader, outbox
        self._on_queued = on_queued or (lambda: None)

    # ------------------------------------------------------------------ enrol ------
    def enrol(self, form: EnrolForm, progress: Progress, cancel: threading.Event | None = None) -> EnrolResult:
        cfg = self.cfg
        step = "validate"
        try:
            progress("validate", "start", "")
            name = validators.asset_name(form.name)
            product_id = validators.product_id(form.product_id)
            room = validators.room_no(form.room_no)
            image_b64 = images.prepare_upload(form.image_path) if form.image_path else None
            want_lock = bool(form.lock and cfg.tag_lock_enabled)
            progress("validate", "done", "")

            step = "present"
            progress("present", "start", "Hold the tag on the reader")
            uid = self.reader.wait_for_tag(30.0, cancel)
            progress("present", "done", uid)

            step = "identify"
            progress("identify", "start", "")
            self.reader.identify()
            progress("identify", "done", "NTAG216")

            token = os.urandom(layout.TOKEN_BYTES)
            d = crypto.seal_product_id(cfg.tag_key, token, product_id)
            link = layout.build_link(cfg.public_host, token, d, scheme=cfg.link_scheme)
            tlv = layout.build_ndef_tlv(link)

            step = "write"
            progress("write", "start", f"{layout.pages_for(tlv)} pages")
            pages = self.reader.write_ndef(tlv)
            progress("write", "done", "")

            step = "verify"
            progress("verify", "start", "")
            try:
                self.reader.verify_ndef(tlv)
            except ntag.NtagError as first:
                log.warning("read-back failed once (%s); rewriting", first)
                self.reader.write_ndef(tlv)
                self.reader.verify_ndef(tlv)
            progress("verify", "done", "")

            step = "queue"
            progress("queue", "start", "")
            row_id = self.outbox.enqueue(self._payload(name, room, token, product_id, uid, link, image_b64, want_lock),
                                         name=name, room_no=room)
            progress("queue", "done", "")
            self._on_queued()
        except Exception as exc:  # noqa: BLE001
            log.error("enrolment failed at %s", step, exc_info=True)
            progress(step, "fail", _friendly(exc))
            raise EnrolError(step, _friendly(exc), f"{type(exc).__name__}: {exc}") from exc

        result = EnrolResult(row_id, uid, link, pages, locked=False)
        if not want_lock:
            progress("lock", "skip", "Tag left rewritable")
            return result
        return self._lock(result, progress)

    # ------------------------------------------------------------------- lock ------
    def _lock(self, result: EnrolResult, progress: Progress) -> EnrolResult:
        progress("lock", "start", "This is permanent")
        try:
            self.reader.lock(result.pages)
            progress("lock", "done", "")
            result.locked = True
        except Exception as exc:  # noqa: BLE001
            log.error("lock failed", exc_info=True)
            result.lock_error = _friendly(exc)
            progress("lock", "fail", result.lock_error)
        return result

    def retry_lock(self, result: EnrolResult, progress: Progress, cancel: threading.Event | None = None) -> EnrolResult:
        """The tag was written and registered but the lock step failed. Present the SAME tag."""
        uid = self.reader.wait_for_tag(30.0, cancel)
        if uid != result.uid:
            raise EnrolError("lock", "That is a different tag. Present the one that was just written.")
        result.lock_error = None
        return self._lock(result, progress)

    # ---------------------------------------------------------------- payload ------
    def _payload(self, name, room, token, product_id, uid, link, image_b64, lock_requested) -> dict:
        cfg = self.cfg
        token_hash = crypto.sha256_hex(token)
        sealed = crypto.seal(
            json.dumps({"product_id": product_id, "tag_uid": uid, "link": link}).encode(),
            cfg.field_recipient_pub, f"enrol|{cfg.device_id}|{token_hash}".encode())
        return {"schema": "dsoreg.enrol.v1", "name": name, "room_no": room, "token_hash": token_hash,
                "sealed": crypto.b64u(sealed), "image": image_b64, "lock_requested": lock_requested}
