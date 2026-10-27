"""Operator PIN: only someone who knows it can write tags with this device.

The PIN is stored as an Argon2id hash. Failed attempts are counted on disk and lock the
app out for a growing interval, so a PIN can't be ground out by hand-tapping the keypad
(and restarting the app does not reset the counter).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MAX_FREE_ATTEMPTS = 5
_hasher = PasswordHasher()


class PinLocked(Exception):
    def __init__(self, seconds: int):
        super().__init__(f"Too many wrong PINs. Try again in {seconds} s.")
        self.seconds = seconds


class PinPolicyError(ValueError):
    pass


def check_policy(pin: str) -> None:
    if not pin.isdigit() or not 6 <= len(pin) <= 12:
        raise PinPolicyError("The PIN must be 6 to 12 digits.")
    if len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210":
        raise PinPolicyError("That PIN is too easy to guess.")


class PinGate:
    def __init__(self, home: Path):
        self._hash_path = home / "pin.hash"
        self._state_path = home / "pin.state"

    def is_set(self) -> bool:
        return self._hash_path.exists()

    def set_pin(self, pin: str) -> None:
        check_policy(pin)
        self._hash_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._hash_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(_hasher.hash(pin))
        self._save({"failures": 0, "locked_until": 0})

    def _load(self) -> dict:
        try:
            return json.loads(self._state_path.read_text(encoding="ascii"))
        except (OSError, ValueError):
            return {"failures": 0, "locked_until": 0}

    def _save(self, state: dict) -> None:
        fd = os.open(self._state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            json.dump(state, fh)

    def seconds_locked(self) -> int:
        return max(0, int(self._load()["locked_until"] - time.time()))

    def verify(self, pin: str) -> bool:
        state = self._load()
        remaining = int(state["locked_until"] - time.time())
        if remaining > 0:
            raise PinLocked(remaining)
        try:
            ok = _hasher.verify(self._hash_path.read_text(encoding="ascii"), pin)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False
        if ok:
            self._save({"failures": 0, "locked_until": 0})
            return True
        failures = state["failures"] + 1
        locked_until = 0
        if failures >= MAX_FREE_ATTEMPTS:
            locked_until = time.time() + min(900, 30 * 2 ** (failures - MAX_FREE_ATTEMPTS))
        self._save({"failures": failures, "locked_until": locked_until})
        if locked_until:
            raise PinLocked(int(locked_until - time.time()))
        return False
