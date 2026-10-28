"""Tag readers: the real PN532 and a simulator that behaves like one.

The simulator is not a mock of the app — it is a second implementation of the same
interface, with an in-memory NTAG216, lock-bit enforcement and optional fault injection.
It lets the whole app (and the test suite, and a Windows demo build) run without hardware.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Protocol

from . import ntag
from .layout import NDEF_START_PAGE

log = logging.getLogger("dso.reader")


class ReaderError(RuntimeError):
    pass


class Reader(Protocol):
    name: str
    def wait_for_tag(self, timeout: float, cancel: threading.Event | None = None) -> str: ...
    def identify(self) -> None: ...
    def write_ndef(self, tlv: bytes) -> int: ...
    def verify_ndef(self, tlv: bytes) -> None: ...
    def lock(self, ndef_pages: int) -> None: ...
    def close(self) -> None: ...


class PN532Reader:
    name = "PN532"

    def __init__(self, *, interface: str = "i2c", delay: float = 0.05, retries: int = 3, mode: str = "library",
                 allow_clone: bool = False):
        try:
            import board  # type: ignore
            import busio  # type: ignore
        except ImportError as exc:
            raise ReaderError("Hardware libraries are not installed. On the Pi: pip install "
                              "adafruit-blinka adafruit-circuitpython-pn532") from exc
        try:
            if interface == "spi":
                import digitalio  # type: ignore
                from adafruit_pn532.spi import PN532_SPI  # type: ignore
                spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
                self._pn = PN532_SPI(spi, digitalio.DigitalInOut(board.D5), debug=False)
            else:
                from adafruit_pn532.i2c import PN532_I2C  # type: ignore
                self._pn = PN532_I2C(busio.I2C(board.SCL, board.SDA), debug=False)
            self._pn.SAM_configuration()
        except Exception as exc:  # noqa: BLE001
            raise ReaderError(f"Could not start the PN532 over {interface.upper()}: {exc}") from exc
        self._delay, self._retries, self._mode = delay, retries, mode
        self._allow_clone, self._genuine = allow_clone, True

    def wait_for_tag(self, timeout: float, cancel: threading.Event | None = None) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cancel is not None and cancel.is_set():
                raise ReaderError("Cancelled.")
            uid = self._pn.read_passive_target(timeout=0.5)
            if uid:
                return bytes(uid).hex().upper()
        raise ReaderError("No tag was presented in time.")

    def identify(self) -> None:
        self._genuine = ntag.is_genuine(ntag.assert_ntag216(self._pn, allow_clone=self._allow_clone))

    def write_ndef(self, tlv: bytes) -> int:
        return ntag.write_ndef(self._pn, tlv, delay=self._delay, retries=self._retries, mode=self._mode)

    def verify_ndef(self, tlv: bytes) -> None:
        ntag.verify_ndef(self._pn, tlv)

    def lock(self, ndef_pages: int) -> None:
        if not self._genuine:
            raise ReaderError("Locking is only supported on genuine NXP tags; this one is a clone.")
        ntag.lock_data_pages(self._pn, ndef_pages, delay=self._delay, retries=self._retries, mode=self._mode)

    def close(self) -> None:
        try:
            self._pn.power_down()
        except Exception:  # noqa: BLE001
            pass


class SimulatedReader:
    """An NTAG216 in memory. `fail_writes` makes the next N page writes fail, to exercise retry
    and error paths; `present` is the tag currently 'on the reader'."""
    name = "Simulator"

    def __init__(self, *, delay: float = 0.0, retries: int = 3, tag_delay: float = 0.4,
                 auto_new_tag: bool = False):
        self.auto_new_tag = auto_new_tag      # present a fresh blank tag on every wait_for_tag()
        self._pages: dict[int, bytes] = {}
        self._locked: set[int] = set()
        self._uid = ""
        self.fail_writes = 0
        self.present = True
        self._delay, self._retries, self._tag_delay = delay, retries, tag_delay
        self.write_log: list[int] = []

    def new_tag(self, uid: str | None = None) -> str:
        self._pages, self._locked = {}, set()
        self._uid = uid or ("04" + os.urandom(6).hex().upper())
        self.present = True
        return self._uid

    def wait_for_tag(self, timeout: float, cancel: threading.Event | None = None) -> str:
        end = time.time() + min(timeout, self._tag_delay)
        while time.time() < end:
            if cancel is not None and cancel.is_set():
                raise ReaderError("Cancelled.")
            time.sleep(0.02)
        if not self.present:
            raise ReaderError("No tag was presented in time.")
        if self.auto_new_tag or not self._uid:
            self.new_tag()
        return self._uid

    def identify(self) -> None:
        return None

    def _write(self, page: int, data: bytes) -> None:
        attempts = 0
        while True:
            attempts += 1
            if page in self._locked:
                raise ntag.WriteFailed(page, attempts, "page is locked")
            if self.fail_writes > 0:
                self.fail_writes -= 1
                if attempts >= self._retries:
                    raise ntag.WriteFailed(page, attempts, "simulated CRC error")
                continue
            self._pages[page] = bytes(data)
            self.write_log.append(page)
            return

    def write_ndef(self, tlv: bytes) -> int:
        for i in range(len(tlv) // 4):
            self._write(NDEF_START_PAGE + i, tlv[i * 4:(i + 1) * 4])
        return len(tlv) // 4

    def read_back(self, pages: int) -> bytes:
        return b"".join(self._pages.get(NDEF_START_PAGE + i, b"\0\0\0\0") for i in range(pages))

    def verify_ndef(self, tlv: bytes) -> None:
        got = self.read_back(len(tlv) // 4)
        if got != tlv:
            i = next(i for i, (a, b) in enumerate(zip(tlv, got)) if a != b)
            raise ntag.NtagError(f"read-back mismatch at byte {i}: wrote {tlv[i]:#04x}, read {got[i]:#04x}")

    def lock(self, ndef_pages: int) -> None:
        self._locked |= set(range(3, 0x10)) | set(range(NDEF_START_PAGE, NDEF_START_PAGE + ndef_pages))

    @property
    def locked(self) -> bool:
        return bool(self._locked)

    def close(self) -> None:
        return None


def open_reader(cfg) -> Reader:
    if cfg.reader == "sim":
        return SimulatedReader(delay=cfg.page_write_delay, retries=cfg.page_write_retries, auto_new_tag=True)
    return PN532Reader(interface=cfg.pn532_interface, delay=cfg.page_write_delay,
                       retries=cfg.page_write_retries, mode=cfg.write_mode, allow_clone=cfg.allow_clone_tags)
