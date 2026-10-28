"""On-screen keyboard.

Tap any registered text box and the keyboard slides up, exactly like on a phone. Keys are
big (touch targets), shift is one-shot, and names get automatic capital letters. A
separate PIN pad is used for the unlock screen.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from . import theme

ROW_DIGITS = list("1234567890")
ROWS_LETTERS = [list("qwertyuiop"), list("asdfghjkl"), list("zxcvbnm")]
ROWS_SYMBOLS = [list("-_./,'()&+"), list("#@:;!?\"*=%"), list("<>[]{}~^|\\")]
# Characters the register rejects are simply not on the keyboard where the field forbids them.
BLOCKED = {"product": set("',()&+#@:;!?\"*=%<>[]{}~^|\\ ,"), "room": set("',()&+#@:;!?\"*=%<>[]{}~^|\\,"), "name": set("#@:;!?\"*=%<>[]{}~^|\\")}


def _inner(entry):
    return getattr(entry, "_entry", entry)


class KeyboardManager:
    def __init__(self, host: ctk.CTkFrame, on_change: Callable[[], None] | None = None):
        self.host = host
        self._on_change = on_change or (lambda: None)
        self.target = None
        self.kind = "text"
        self.autocap = False
        self.upper = False
        self.shift = False
        self.symbols = False
        self._keys: list[tuple[ctk.CTkButton, str]] = []
        self._dispatch: dict = {}
        host.configure(fg_color=theme.SURFACE_2, corner_radius=0)

    # ------------------------------------------------------------------ wiring ----
    def register(self, entry, *, kind: str = "text", autocap: bool = False, upper: bool = False,
                 on_done: Callable | None = None, next_to=None) -> None:
        """`next_to`: the box the Done key moves on to (it then reads "Next" and the keyboard stays up)."""
        self._dispatch[id(entry)] = (kind, autocap, upper, on_done, next_to)
        # A tap opens the keyboard. FocusIn is deliberately NOT used: the window manager hands focus
        # back to the last text box whenever a dialog closes, which popped the keyboard up by itself.
        _inner(entry).bind("<Button-1>", lambda _e, en=entry: self.show(en), add="+")
        _inner(entry).bind("<Return>", lambda _e, en=entry: self.done(), add="+")

    def visible(self) -> bool:
        return bool(self.host.winfo_ismapped())

    def show(self, entry) -> None:
        self.target = entry
        self.kind, self.autocap, self.upper, self._done_cb, self._next_to = self._dispatch[id(entry)]
        self.symbols = False
        try:
            _inner(entry).focus_set()
            _inner(entry).icursor("end")
        except Exception:  # noqa: BLE001
            pass
        self.shift = self.autocap and self._at_word_start()
        self._build()
        if not self.visible():
            self.host.grid()
        self._on_change()

    def hide(self) -> None:
        self.host.grid_remove()
        self.target = None
        self._on_change()

    def done(self) -> None:
        nxt = getattr(self, "_next_to", None)
        if nxt is not None:
            self.show(nxt)
            return
        cb = getattr(self, "_done_cb", None)
        self.hide()
        if cb:
            cb()

    # ------------------------------------------------------------------ typing ----
    def _at_word_start(self) -> bool:
        if self.target is None:
            return False
        text = self.target.get()
        pos = self._cursor()
        return pos == 0 or text[pos - 1:pos] in (" ", "")

    def _cursor(self) -> int:
        try:
            return _inner(self.target).index(tk.INSERT)
        except Exception:  # noqa: BLE001
            return len(self.target.get())

    def type_char(self, ch: str) -> None:
        if self.target is None:
            return
        if (self.shift or self.upper) and ch.isalpha():
            ch = ch.upper()
        inner = _inner(self.target)
        try:
            if inner.selection_present():
                inner.delete("sel.first", "sel.last")
        except Exception:  # noqa: BLE001
            pass
        self.target.insert(self._cursor(), ch)
        self.shift = self.autocap and ch == " "
        self._refresh_caps()

    def backspace(self) -> None:
        if self.target is None:
            return
        inner = _inner(self.target)
        try:
            if inner.selection_present():
                inner.delete("sel.first", "sel.last")
                return
        except Exception:  # noqa: BLE001
            pass
        pos = self._cursor()
        if pos > 0:
            self.target.delete(pos - 1)
        if self.autocap:
            self.shift = self._at_word_start()
            self._refresh_caps()

    def toggle_shift(self) -> None:
        self.shift = not self.shift
        self._refresh_caps()

    def toggle_symbols(self) -> None:
        self.symbols = not self.symbols
        self._build()

    # ------------------------------------------------------------------ layout ----
    def compact(self) -> bool:
        try:
            top = self.host.winfo_toplevel()
            scale = top._get_window_scaling() if hasattr(top, "_get_window_scaling") else 1.0
            return top.winfo_height() / scale < 560
        except Exception:  # noqa: BLE001
            return False

    def _key(self, parent, label, command, *, width=1, accent=False, tall=False):
        small = self.compact()
        btn = ctk.CTkButton(
            parent, text=label, command=command, width=(40 if small else 44) * width, height=38 if small else 50,
            corner_radius=9, font=theme.font(16 if small else 20, "bold" if accent else "normal"),
            fg_color=theme.ACCENT if accent else theme.SURFACE, hover_color=theme.ACCENT_HOVER if accent else theme.LINE,
            text_color=theme.ACCENT_INK if accent else theme.TEXT, border_width=1, border_color=theme.LINE)
        return btn

    def _build(self) -> None:
        for child in self.host.winfo_children():
            child.destroy()
        self._keys = []
        blocked = BLOCKED.get(self.kind, set())
        wrap = ctk.CTkFrame(self.host, fg_color="transparent")
        wrap.pack(padx=6, pady=3 if self.compact() else 6)

        def row(chars, extra_left=None, extra_right=None):
            r = ctk.CTkFrame(wrap, fg_color="transparent")
            r.pack(pady=2)
            if extra_left:
                extra_left(r).pack(side="left", padx=2)
            for ch in chars:
                if ch in blocked:
                    continue
                b = self._key(r, ch.upper() if ((self.shift or self.upper) and ch.isalpha()) else ch,
                              lambda c=ch: self.type_char(c))
                b.pack(side="left", padx=2)
                self._keys.append((b, ch))
            if extra_right:
                extra_right(r).pack(side="left", padx=2)
            return r

        if self.kind == "numeric":
            for chars in ("123", "456", "789"):
                row(list(chars))
            row(["0"], extra_right=lambda r: self._key(r, "⌫", self.backspace, width=1))
            return
        row(ROW_DIGITS)
        rows = ROWS_SYMBOLS if self.symbols else ROWS_LETTERS
        row(rows[0])
        row(rows[1])
        row(rows[2],
            extra_left=None if self.symbols else (lambda r: self._key(r, "⇧", self.toggle_shift, width=1.5)),
            extra_right=lambda r: self._key(r, "⌫", self.backspace, width=1.5))
        bottom = ctk.CTkFrame(wrap, fg_color="transparent")
        bottom.pack(pady=2)
        self._key(bottom, "ABC" if self.symbols else "?123", self.toggle_symbols, width=1.8).pack(side="left", padx=2)
        if "-" not in blocked:
            self._key(bottom, "-", lambda: self.type_char("-")).pack(side="left", padx=2)
        if " " not in blocked:
            self._key(bottom, "space", lambda: self.type_char(" "), width=7).pack(side="left", padx=2)
        else:
            self._key(bottom, "_", lambda: self.type_char("_"), width=7).pack(side="left", padx=2)
        self._key(bottom, ".", lambda: self.type_char(".")).pack(side="left", padx=2)
        self._key(bottom, "/", lambda: self.type_char("/")).pack(side="left", padx=2)
        self._key(bottom, "Next" if getattr(self, "_next_to", None) is not None else "Done", self.done,
                  width=2.2, accent=True).pack(side="left", padx=2)
        self._refresh_caps()

    def _refresh_caps(self) -> None:
        for btn, ch in self._keys:
            if ch.isalpha():
                btn.configure(text=ch.upper() if (self.shift or self.upper) else ch)


class PinPad(ctk.CTkFrame):
    """A numeric keypad that collects digits and shows them as dots."""

    def __init__(self, master, *, length: int = 12, on_enter: Callable[[str], None], compact: bool = False):
        super().__init__(master, fg_color="transparent")
        self._digits = ""
        self._max = length
        self._on_enter = on_enter
        self.dots = ctk.CTkLabel(self, text="", font=theme.font(26 if compact else 30, "bold"), text_color=theme.TEXT,
                                 height=34 if compact else 48)
        self.dots.pack(pady=(0, 2 if compact else 8))
        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack()
        keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "OK"]
        for i, k in enumerate(keys):
            accent = k == "OK"
            ctk.CTkButton(
                grid, text=k, width=84 if compact else 88, height=50 if compact else 64, corner_radius=14,
                font=theme.font(22 if compact else 24, "bold"),
                fg_color=theme.ACCENT if accent else theme.SURFACE, hover_color=theme.ACCENT_HOVER if accent else theme.LINE,
                text_color=theme.ACCENT_INK if accent else theme.TEXT, border_width=1, border_color=theme.LINE,
                command=lambda key=k: self._press(key)).grid(row=i // 3, column=i % 3, padx=4, pady=3 if compact else 5)

    def _press(self, key: str) -> None:
        if key == "⌫":
            self._digits = self._digits[:-1]
        elif key == "OK":
            digits, self._digits = self._digits, ""
            self._render()
            self._on_enter(digits)
            return
        elif len(self._digits) < self._max:
            self._digits += key
        self._render()

    def _render(self) -> None:
        self.dots.configure(text="●" * len(self._digits) if self._digits else " ")

    def clear(self) -> None:
        self._digits = ""
        self._render()
