"""Background sender. The request is signed at SEND time, never at enqueue time: the server
only accepts timestamps within ~90 s, and a record may wait in the outbox for hours."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

import requests

from .crypto import sign_request

log = logging.getLogger("dso.drainer")
PATH = "/api/v1/enrol"
# The server said "no" about THIS record. Retrying the same bytes cannot change the answer.
PERMANENT = {"already_enrolled", "bad_schema", "bad_seal", "bad_link", "invalid_field", "invalid_image",
             "payload_too_large", "idempotency_key_reuse", "device_revoked", "bad_signature"}


class Drainer:
    def __init__(self, cfg, outbox, *, on_change: Callable[[], None] | None = None):
        self._cfg, self._box, self._on_change = cfg, outbox, on_change or (lambda: None)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="drainer", daemon=True)
        self.clock_drift: float | None = None
        self.online: bool | None = None
        self._session = requests.Session()
        self._verify = cfg.ca_bundle or True

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()

    def kick(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.drain_once()
            except Exception:  # noqa: BLE001 - the loop must never die
                log.exception("drainer iteration failed")
            self._wake.wait(timeout=10)
            self._wake.clear()

    def check_clock(self) -> float | None:
        try:
            r = self._session.get(self._cfg.backend_url + "/health", timeout=(5, 10), verify=self._verify)
            r.raise_for_status()
            from datetime import datetime
            server = datetime.fromisoformat(r.json()["server_time"].replace("Z", "+00:00")).timestamp()
            self.clock_drift = time.time() - server
            self.online = True
        except Exception as exc:  # noqa: BLE001
            self.online = False
            log.warning("health check failed: %s", exc)
        return self.clock_drift

    def drain_once(self) -> int:
        sent = 0
        for row in self._box.due():
            if self._stop.is_set():
                break
            sent += self._send(row)
        return sent

    def _send(self, row) -> int:
        body = bytes(row["body"])
        ts = str(int(time.time()))
        headers = {
            "Content-Type": "application/json", "X-Device-Id": self._cfg.device_id, "X-Timestamp": ts,
            "X-Idempotency-Key": row["idem_key"],
            "X-Signature": sign_request(self._cfg.device_key, "POST", PATH, ts, row["idem_key"], body),
        }
        try:
            resp = self._session.post(self._cfg.backend_url + PATH, data=body, headers=headers,
                                      timeout=(5, 30), verify=self._verify)
        except requests.RequestException as exc:
            self.online = False
            self._box.retry_later(row["id"], f"network: {type(exc).__name__}", attempts=row["attempts"] + 1)
            self._on_change()
            return 0
        self.online = True
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code in (200, 201):
            self._box.mark_sent(row["id"], data.get("asset_id"))
            self._on_change()
            return 1
        code = (data.get("error") or {}).get("code", "")
        message = (data.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
        if code == "stale_timestamp":
            message = "Device clock is wrong: " + message
        if resp.status_code < 500 and resp.status_code not in (408, 429) and (code in PERMANENT or resp.status_code in (400, 403, 413, 422)):
            self._box.mark_failed(row["id"], f"{code or resp.status_code}: {message}")
        else:
            self._box.retry_later(row["id"], f"{code or resp.status_code}: {message}", attempts=row["attempts"] + 1)
        self._on_change()
        return 0
