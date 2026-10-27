"""Optional product photo: shrink and re-encode on the Pi so uploads are small, and the
server (which re-encodes again and trusts nothing) has little to do."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 40_000_000
MAX_INPUT_BYTES = 25 * 1024 * 1024
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


class ImageError(ValueError):
    pass


def _open(path: str) -> Image.Image:
    p = Path(path)
    if p.suffix.lower() not in IMAGE_SUFFIXES:
        raise ImageError("Choose a JPEG or PNG photo.")
    if p.stat().st_size > MAX_INPUT_BYTES:
        raise ImageError("That photo is too large (25 MB max).")
    try:
        img = Image.open(p)
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageError("That file could not be read as a photo.") from None
    if img.format not in {"JPEG", "PNG"}:
        raise ImageError("Choose a JPEG or PNG photo.")
    return img


def prepare_upload(path: str) -> str:
    """Return base64 of a <=1024px JPEG with no metadata."""
    img = _open(path).convert("RGB")
    img.thumbnail((1024, 1024))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82, optimize=True)
    data = out.getvalue()
    if len(data) > 1_800_000:
        raise ImageError("That photo is still too large after shrinking.")
    return base64.b64encode(data).decode("ascii")


def preview(path: str, size: tuple[int, int] = (220, 165)) -> Image.Image:
    img = _open(path).convert("RGB")
    img.thumbnail(size)
    return img
