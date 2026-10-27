"""A touch-friendly photo picker. The native file dialog is built for a mouse; this one has
big rows, thumbnails, quick links to Pictures and USB sticks, and shows only photos."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from .. import images
from . import theme

PAGE = 7


def _quick_locations() -> list[tuple[str, Path]]:
    home = Path.home()
    spots = [("Home", home), ("Pictures", home / "Pictures"), ("Downloads", home / "Downloads"),
             ("Desktop", home / "Desktop")]
    for base in (Path("/media") / home.name, Path("/run/media") / home.name, Path("/media")):
        if base.is_dir():
            try:
                spots += [(f"USB: {p.name}", p) for p in sorted(base.iterdir()) if p.is_dir()]
            except OSError:
                pass
    return [(label, p) for label, p in spots if p.is_dir()]


class ImagePicker(ctk.CTkToplevel):
    def __init__(self, parent, on_pick: Callable[[str], None], start: Path | None = None):
        super().__init__(parent)
        self.title("Choose a photo")
        self.geometry(f"{max(parent.winfo_width(), 760)}x{max(parent.winfo_height(), 460)}+{parent.winfo_rootx()}+{parent.winfo_rooty()}")
        self.configure(fg_color=theme.BG)
        self.transient(parent)
        self.after(150, self._grab)
        self._on_pick = on_pick
        self._cwd = start if start and start.is_dir() else Path.home()
        self._page = 0
        self._refs: list = []

        top = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=0)
        top.pack(fill="x")
        ctk.CTkLabel(top, text="Choose a photo", font=theme.font(20, "bold"), text_color=theme.TEXT).pack(side="left", padx=14, pady=10)
        ctk.CTkButton(top, text="Cancel", width=90, height=40, fg_color=theme.SURFACE_2, text_color=theme.TEXT,
                      hover_color=theme.LINE, command=self.destroy).pack(side="right", padx=10)

        quick = ctk.CTkFrame(self, fg_color="transparent")
        quick.pack(fill="x", padx=10, pady=(8, 0))
        for label, path in _quick_locations():
            ctk.CTkButton(quick, text=label, height=38, width=80, corner_radius=19, fg_color=theme.SURFACE,
                          text_color=theme.TEXT, hover_color=theme.LINE, border_width=1, border_color=theme.LINE,
                          command=lambda p=path: self._go(p)).pack(side="left", padx=3)

        self._path_label = ctk.CTkLabel(self, text="", anchor="w", text_color=theme.MUTED, font=theme.font(13))
        self._path_label.pack(fill="x", padx=14, pady=(8, 0))
        self._list = ctk.CTkFrame(self, fg_color="transparent")
        self._list.pack(fill="both", expand=True, padx=10, pady=6)
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.pack(fill="x", padx=10, pady=(0, 10))
        self._prev = ctk.CTkButton(nav, text="← Previous", height=42, width=120, fg_color=theme.ACCENT, text_color=theme.ACCENT_INK,
                                   hover_color=theme.ACCENT_HOVER, text_color_disabled=theme.MUTED, command=lambda: self._turn(-1))
        self._prev.pack(side="left")
        self._count = ctk.CTkLabel(nav, text="", text_color=theme.MUTED)
        self._count.pack(side="left", expand=True)
        self._next = ctk.CTkButton(nav, text="Next →", height=42, width=120, fg_color=theme.ACCENT, text_color=theme.ACCENT_INK,
                                   hover_color=theme.ACCENT_HOVER, text_color_disabled=theme.MUTED, command=lambda: self._turn(1))
        self._next.pack(side="right")
        self._render()

    def _grab(self) -> None:
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass

    def _go(self, path: Path) -> None:
        self._cwd, self._page = path, 0
        self._render()

    def _turn(self, step: int) -> None:
        self._page += step
        self._render()

    def _entries(self) -> list[tuple[str, Path]]:
        items: list[tuple[str, Path]] = []
        if self._cwd.parent != self._cwd:
            items.append(("up", self._cwd.parent))
        try:
            children = sorted(self._cwd.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return items
        items += [("dir", p) for p in children if p.is_dir() and not p.name.startswith(".")]
        items += [("img", p) for p in children if p.is_file() and p.suffix.lower() in images.IMAGE_SUFFIXES]
        return items

    def _render(self) -> None:
        for child in self._list.winfo_children():
            child.destroy()
        self._refs.clear()
        entries = self._entries()
        pages = max(1, -(-len(entries) // PAGE))
        self._page = max(0, min(self._page, pages - 1))
        self._path_label.configure(text=str(self._cwd))
        self._count.configure(text=f"Page {self._page + 1} of {pages}")
        self._prev.configure(state="normal" if self._page else "disabled")
        self._next.configure(state="normal" if self._page < pages - 1 else "disabled")
        for kind, path in entries[self._page * PAGE:(self._page + 1) * PAGE]:
            row = ctk.CTkFrame(self._list, fg_color=theme.SURFACE, corner_radius=10, border_width=1, border_color=theme.LINE)
            row.pack(fill="x", pady=3)
            if kind in ("up", "dir"):
                label = "⬆  Parent folder" if kind == "up" else f"📁  {path.name}"
                btn = ctk.CTkButton(row, text=label, anchor="w", height=48, fg_color="transparent", text_color=theme.TEXT,
                                    hover_color=theme.SURFACE_2, font=theme.font(17), command=lambda p=path: self._go(p))
                btn.pack(fill="x", padx=4, pady=2)
            else:
                thumb = self._thumb(path)
                btn = ctk.CTkButton(row, text=f"  {path.name}", image=thumb, compound="left", anchor="w", height=62,
                                    fg_color="transparent", text_color=theme.TEXT, hover_color=theme.SURFACE_2,
                                    font=theme.font(17), command=lambda p=path: self._choose(p))
                btn.pack(fill="x", padx=4, pady=2)

    def _thumb(self, path: Path):
        try:
            img = images.preview(str(path), (72, 54))
            ref = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._refs.append(ref)
            return ref
        except Exception:  # noqa: BLE001
            return None

    def _choose(self, path: Path) -> None:
        self._on_pick(str(path))
        self.destroy()
