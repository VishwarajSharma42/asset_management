"""Uploaded asset images are untrusted input.

An image is only ever accepted as a decoded pixel grid and written back out as a fresh
JPEG: no original bytes, no metadata (EXIF/GPS), no embedded scripts or polyglot
payloads survive. The file is named by the SHA-256 of what we wrote, never by anything
the client sent.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import re
import tempfile
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..errors import PayloadTooLarge, Unprocessable

Image.MAX_IMAGE_PIXELS = 16_000_000
MAX_DECODED_BYTES = 2 * 1024 * 1024
MAX_EDGE = 1024
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def process_upload(b64: str, media_dir: str) -> str:
    """Validate, re-encode and store an image. Returns its sha256 (the filename stem)."""
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise Unprocessable("The image could not be read.", code="invalid_image") from None
    if len(raw) > MAX_DECODED_BYTES:
        raise PayloadTooLarge("The image is too large.")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            if probe.format not in {"JPEG", "PNG"}:
                raise Unprocessable("Only JPEG and PNG images are accepted.", code="invalid_image")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as img:
            img.load()
            img = img.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise Unprocessable("The image could not be read.", code="invalid_image") from None

    img.thumbnail((MAX_EDGE, MAX_EDGE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82, optimize=True)  # no exif= argument: metadata dropped
    data = out.getvalue()
    digest = hashlib.sha256(data).hexdigest()

    folder = Path(media_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{digest}.jpg"
    if not target.exists():
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return digest
