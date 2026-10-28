"""NTAG216 over a PN532: raw commands, the paced page write, the read-back compare and the
irreversible lock-bit sequence.

Adapted from the v2 enroller's ntag.py, with these deliberate changes:

  * Every failed attempt is logged with its full exception text and traceback, and the
    final error says WHICH page failed after HOW MANY attempts. Writes were failing on
    some boards with a truncated "status 2", and that told nobody anything.
  * `write_mode` can be "library" (adafruit's ntag2xx_write_block, the default) or "raw"
    (an NTAG WRITE sent through InCommunicateThru, the same command mode every other
    operation here uses). "raw" is EXPERIMENTAL and has not been exercised on hardware.
  * read_pages() can read below page 04h. v2's fast_read() refused page 02h, which its own
    lock sequence needed.
  * Locking sets the static and dynamic lock BITS only. It does not touch the
    configuration pages: this system has no counter or mirror to protect.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("dso.ntag")

CMD_IN_COMMUNICATE_THRU = 0x42
CMD_GET_VERSION = 0x60
CMD_FAST_READ = 0x3A
CMD_WRITE = 0xA2

NXP_VENDOR_ID = 0x04
NTAG_TYPE = 0x04
NTAG216_STORAGE_SIZE = 0x13
USER_PAGE_FIRST = 0x04
USER_PAGE_LAST = 0xE1
PN532_FRAME_BYTES = 252
STATIC_LOCK_PAGE = 0x02
DYNAMIC_LOCK_PAGE = 0xE2


class NtagError(RuntimeError):
    """Any failure talking to the tag."""


class TagRejected(NtagError):
    """Not an NTAG216. Do not use this tag."""


class WriteFailed(NtagError):
    def __init__(self, page: int, attempts: int, cause: str):
        super().__init__(f"write failed at page {page:#04x} after {attempts} attempts: {cause}")
        self.page, self.attempts, self.cause = page, attempts, cause


def transceive(pn532, payload: list[int], response_length: int) -> bytes:
    """Raw ISO14443A-3 command via InCommunicateThru (42h). Returns the response without the
    PN532 status byte. (InDataExchange, 40h, mistakes some NTAG command bytes for MIFARE
    authentication — see the v2 notes.)"""
    resp = pn532.call_function(CMD_IN_COMMUNICATE_THRU, params=list(payload),
                               response_length=response_length + 1)
    if resp is None or len(resp) < 1 or resp[0] != 0x00:
        raise NtagError(f"InCommunicateThru status {resp[0] if resp else 'none'}")
    return bytes(resp[1:])


def is_genuine(version: bytes) -> bool:
    return version[1] == NXP_VENDOR_ID


def assert_ntag216(pn532, *, allow_clone: bool = False) -> bytes:
    """Refuse anything that is not an NTAG216. With `allow_clone`, a tag that reports the NTAG216
    type and size but a non-NXP vendor byte is accepted (cheap compatible tags): it holds NDEF
    like the real thing, but its lock and configuration behaviour is not guaranteed."""
    version = transceive(pn532, [CMD_GET_VERSION], 8)
    if len(version) < 8:
        raise TagRejected(f"GET_VERSION returned {len(version)} bytes, expected 8")
    if not is_genuine(version):
        if not allow_clone:
            raise TagRejected(f"vendor byte {version[1]:#04x} is not NXP (a clone tag; set allow_clone_tags to use it)")
        if version[2] != NTAG_TYPE:
            raise TagRejected(f"product type {version[2]:#04x} is not an NTAG")
        log.warning("accepting a non-NXP NTAG216 clone (vendor %#04x)", version[1])
    if version[6] != NTAG216_STORAGE_SIZE:
        raise TagRejected(f"storage size {version[6]:#04x} is not NTAG216 ({NTAG216_STORAGE_SIZE:#04x})")
    return version


def read_pages(pn532, start: int, end: int) -> bytes:
    nbytes = (end - start + 1) * 4
    if not 0x00 <= start <= end <= USER_PAGE_LAST or nbytes > PN532_FRAME_BYTES:
        raise NtagError(f"read range {start:#04x}-{end:#04x} is not readable in one frame")
    return transceive(pn532, [CMD_FAST_READ, start, end], nbytes)


def write_page(pn532, page: int, data: bytes, *, delay: float, retries: int, mode: str = "library") -> None:
    if len(data) != 4:
        raise NtagError(f"page write must be exactly 4 bytes, got {len(data)}")
    last = "no attempt made"
    for attempt in range(1, retries + 1):
        try:
            if mode == "raw":
                resp = transceive(pn532, [CMD_WRITE, page, *data], 1)
                if resp and resp[0] != 0x0A:
                    raise NtagError(f"tag answered {resp.hex()} instead of ACK")
            else:
                ok = pn532.ntag2xx_write_block(page, list(data))
                if ok is False:  # the library reports some failures as False, not an exception
                    raise NtagError("ntag2xx_write_block returned False (tag did not acknowledge)")
            time.sleep(delay)
            return
        except Exception as exc:  # noqa: BLE001 - every cause is logged and retried
            last = f"{type(exc).__name__}: {exc}"
            log.warning("page %#04x attempt %d/%d failed: %s", page, attempt, retries, last, exc_info=True)
            if attempt < retries:
                time.sleep(delay * 2)
    raise WriteFailed(page, retries, last)


def write_ndef(pn532, tlv: bytes, *, delay: float, retries: int, mode: str, start_page: int = USER_PAGE_FIRST) -> int:
    if len(tlv) % 4:
        raise NtagError("TLV must be padded to whole pages")
    pages = len(tlv) // 4
    if start_page + pages - 1 > USER_PAGE_LAST:
        raise NtagError("NDEF does not fit on an NTAG216")
    for i in range(pages):
        write_page(pn532, start_page + i, tlv[i * 4:(i + 1) * 4], delay=delay, retries=retries, mode=mode)
    return pages


def verify_ndef(pn532, tlv: bytes, *, start_page: int = USER_PAGE_FIRST) -> None:
    """Read back and BYTE-COMPARE. The NDEF area has no anti-tearing protection in hardware."""
    pages = len(tlv) // 4
    got = read_pages(pn532, start_page, start_page + pages - 1)
    if got[:len(tlv)] != tlv:
        for i, (want, have) in enumerate(zip(tlv, got)):
            if want != have:
                raise NtagError(f"read-back mismatch at byte {i} (page {start_page + i // 4:#04x}): "
                                f"wrote {want:#04x}, read {have:#04x}")
        raise NtagError("read-back is shorter than what was written")


def dynamic_lock_bytes(last_page: int) -> list[int]:
    """Lock bits for pages 10h..E1h in 16-page blocks (NTAG216 geometry). One-way."""
    bits = [0, 0, 0]
    for page in range(0x10, min(last_page, USER_PAGE_LAST) + 1):
        block = (page - 0x10) // 16
        bits[block // 8] |= 1 << (block % 8)
    return bits


def lock_data_pages(pn532, ndef_pages: int, *, delay: float, retries: int, mode: str) -> None:
    """IRREVERSIBLE. Sets the static lock bits (pages 03h-0Fh) and the dynamic lock bits for
    every page the record occupies. Afterwards the data area is read-only for good."""
    page02 = read_pages(pn532, STATIC_LOCK_PAGE, STATIC_LOCK_PAGE)[:4]
    # Bytes 0-1 of page 02h belong to the serial number / internal area: written back as read.
    write_page(pn532, STATIC_LOCK_PAGE, bytes([page02[0], page02[1], 0xFF, 0xFF]),
               delay=delay, retries=retries, mode=mode)
    last_page = USER_PAGE_FIRST + ndef_pages - 1
    if last_page >= 0x10:
        current = read_pages(pn532, DYNAMIC_LOCK_PAGE, DYNAMIC_LOCK_PAGE)[:4]
        merged = [c | b for c, b in zip(current[:3], dynamic_lock_bytes(last_page))]
        write_page(pn532, DYNAMIC_LOCK_PAGE, bytes([*merged, current[3]]),
                   delay=delay, retries=retries, mode=mode)
