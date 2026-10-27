"""Colours and fonts. Every colour is a (light, dark) pair, so the app follows the system
appearance and stays readable either way. Sizes are generous: this is used with a finger."""
import customtkinter as ctk

BG = ("#f4f6f8", "#0f1519")
SURFACE = ("#ffffff", "#182027")
SURFACE_2 = ("#eef1f4", "#1f2a33")
LINE = ("#d9e0e6", "#2c3a45")
TEXT = ("#14202b", "#e8eef2")
MUTED = ("#566573", "#9fb0bd")
ACCENT = ("#0b6e74", "#4fc3c9")
ACCENT_HOVER = ("#095a5f", "#7ad6db")
ACCENT_INK = ("#ffffff", "#062a2c")
OK = ("#1a7f4b", "#5fd39a")
OK_SOFT = ("#dff4e8", "#12301f")
BAD = ("#b3261e", "#ff8a80")
BAD_SOFT = ("#fbe3e1", "#3b1714")
WARN = ("#8a5a00", "#f3c46b")
WARN_SOFT = ("#fcefd2", "#36290c")

_fonts: dict = {}


def font(size: int = 16, weight: str = "normal") -> ctk.CTkFont:
    key = (size, weight)
    if key not in _fonts:
        _fonts[key] = ctk.CTkFont(size=size, weight=weight)
    return _fonts[key]


def setup() -> None:
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
