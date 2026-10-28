"""The enrolment window.

Screens:  PIN (set on first run, then unlock)  ->  Main (form + recent tags + status)
          Write dialog (live step-by-step progress while the tag is written)

The window never talks to hardware or the network on the UI thread: writes run in a worker
thread and report through a queue; the sender runs in its own thread.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

from .. import __version__, images, validators
from ..outbox import Outbox
from ..reader import Reader, ReaderError
from ..security import PinGate, PinLocked, PinPolicyError
from ..service import EnrolError, EnrolForm, EnrolResult, EnrolService
from . import theme
from .filepicker import ImagePicker
from .keyboard import KeyboardManager, PinPad

log = logging.getLogger("dso.ui")
IDLE_LOCK_SECONDS = 300
STEP_LABELS = {
    "validate": "Check the details", "present": "Hold the tag on the reader", "identify": "Identify the tag",
    "write": "Write to the tag", "verify": "Check what was written", "queue": "Save the record",
    "lock": "Lock the tag (permanent)",
}


def chip(parent, text: str, kind: str = "neutral", command=None) -> ctk.CTkLabel | ctk.CTkButton:
    fg, ink = {"ok": (theme.OK_SOFT, theme.OK), "bad": (theme.BAD_SOFT, theme.BAD),
               "warn": (theme.WARN_SOFT, theme.WARN), "neutral": (theme.SURFACE_2, theme.MUTED)}[kind]
    if command:
        return ctk.CTkButton(parent, text=text, command=command, height=30, corner_radius=15, fg_color=fg,
                             text_color=ink, hover_color=fg, font=theme.font(13, "bold"))
    return ctk.CTkLabel(parent, text=f" {text} ", height=30, corner_radius=15, fg_color=fg, text_color=ink,
                        font=theme.font(13, "bold"))


class PinScreen(ctk.CTkFrame):
    def __init__(self, app: "App"):
        super().__init__(app.content, fg_color=theme.BG)
        self.app, self.gate = app, app.gate
        self._first: str | None = None
        card = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=18, border_width=1, border_color=theme.LINE)
        card.place(relx=0.5, rely=0.5, anchor="center")
        small = app.winfo_screenheight() < 700
        ctk.CTkLabel(card, text="Asset Enrolment", font=theme.font(22 if small else 26, "bold"), text_color=theme.TEXT).pack(
            padx=40, pady=(12 if small else 24, 0))
        self.msg = ctk.CTkLabel(card, text="", font=theme.font(16), text_color=theme.MUTED, wraplength=320)
        self.msg.pack(pady=(2, 6))
        self.pad = PinPad(card, on_enter=self._entered, compact=small)
        self.pad.pack(padx=24, pady=(0, 12 if small else 22))
        self.refresh()

    def refresh(self) -> None:
        self._first = None
        self.pad.clear()
        self.msg.configure(text_color=theme.MUTED, text=(
            "Enter your PIN to unlock." if self.gate.is_set() else "First start: choose a PIN (6–12 digits)."))

    def _say(self, text: str, bad: bool = True) -> None:
        self.msg.configure(text=text, text_color=theme.BAD if bad else theme.MUTED)

    def _entered(self, pin: str) -> None:
        if not pin:
            return
        if not self.gate.is_set():
            self._create_pin(pin)
            return
        try:
            if self.gate.verify(pin):
                self.app.unlocked()
            else:
                self._say("Wrong PIN. Try again.")
        except PinLocked as exc:
            self._say(f"Too many attempts. Wait {exc.seconds} s.")

    def _create_pin(self, pin: str) -> None:
        if self._first is None:
            try:
                from ..security import check_policy
                check_policy(pin)
            except PinPolicyError as exc:
                self._say(str(exc))
                return
            self._first = pin
            self._say("Enter the same PIN again to confirm.", bad=False)
        elif pin == self._first:
            self.gate.set_pin(pin)
            self.app.unlocked()
        else:
            self._first = None
            self._say("The PINs did not match. Start again.")


class WriteDialog(ctk.CTkToplevel):
    """Shows the live progress of one tag write and what to do next."""

    def __init__(self, app: "App", form: EnrolForm, with_lock: bool):
        super().__init__(app)
        self.app, self.form = app, form
        self.title("Writing tag")
        self.configure(fg_color=theme.BG)
        self.transient(app)
        lw, lh = app.logical_size()
        dw, dh = min(lw - 20, 620), min(lh - (56 if lh < 700 else 20), 520)   # the window frame takes ~30 px on top
        self.geometry(f"{dw}x{dh}+{app.winfo_rootx() + int((app.winfo_width() - dw * app._get_window_scaling()) // 2)}"
                      f"+{app.winfo_rooty() + int((4 if lh < 700 else 10) * app._get_window_scaling())}")
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.after(150, lambda: self.grab_set())
        self.events: queue.Queue = queue.Queue()
        self.cancel_flag = threading.Event()
        self.result: EnrolResult | None = None
        self._busy = True
        self._steps = [s for s in STEP_LABELS if with_lock or s != "lock"]
        self._rows: dict[str, tuple[ctk.CTkLabel, ctk.CTkLabel]] = {}

        ctk.CTkLabel(self, text=form.name, font=theme.font(18, "bold"), text_color=theme.TEXT, wraplength=dw - 40).pack(padx=20, pady=(8, 0))
        ctk.CTkLabel(self, text=f"{form.product_id}  ·  Room {form.room_no}", font=theme.font(14), text_color=theme.MUTED).pack()
        self.banner = ctk.CTkLabel(self, text="Preparing…", font=theme.font(18, "bold"), text_color=theme.ACCENT,
                                   fg_color=theme.SURFACE, corner_radius=12, height=46, wraplength=dw - 60)
        self.banner.pack(fill="x", padx=20, pady=6)
        box = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=12, border_width=1, border_color=theme.LINE)
        box.pack(fill="x", padx=20)
        for step in self._steps:
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=0)
            icon = ctk.CTkLabel(row, text="○", width=28, height=22, font=theme.font(15), text_color=theme.MUTED)
            icon.pack(side="left")
            label = ctk.CTkLabel(row, text=STEP_LABELS[step], height=22, font=theme.font(14), text_color=theme.MUTED, anchor="w")
            label.pack(side="left", fill="x", expand=True)
            self._rows[step] = (icon, label)
        self.buttons = ctk.CTkFrame(self, fg_color="transparent")
        self.buttons.pack(side="bottom", fill="x", padx=20, pady=8)
        self.detail = ctk.CTkLabel(self, text="", font=theme.font(13), text_color=theme.MUTED, wraplength=dw - 44, justify="left")
        self.detail.pack(fill="x", padx=22, pady=(6, 0))
        self._cancel = ctk.CTkButton(self.buttons, text="Cancel", height=48, fg_color=theme.SURFACE_2, text_color=theme.TEXT,
                                     hover_color=theme.LINE, command=self.cancel_flag.set)
        self._cancel.pack(side="right")

        self._spin = 0
        self._active: str | None = None
        self.after(80, self._poll)
        threading.Thread(target=self._work, name="enrol", daemon=True).start()

    # ---- worker ------------------------------------------------------------------------
    def _work(self) -> None:
        try:
            result = self.app.service.enrol(self.form, lambda s, st, d="": self.events.put(("step", s, st, d)),
                                            self.cancel_flag)
            self.events.put(("done", result))
        except EnrolError as exc:
            self.events.put(("error", exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("unexpected error while enrolling")
            self.events.put(("error", EnrolError("write", f"Unexpected error: {exc}", repr(exc))))

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        if self._busy:
            if self._active:
                self._spin = (self._spin + 1) % 4
                self._rows[self._active][0].configure(text="◜◝◞◟"[self._spin], text_color=theme.ACCENT)
            self.after(120, self._poll)

    def _handle(self, ev) -> None:
        kind = ev[0]
        if kind == "step":
            _, step, state, detail = ev
            if step not in self._rows:
                return
            icon, label = self._rows[step]
            if state == "start":
                self._active = step
                label.configure(text_color=theme.TEXT)
                self.banner.configure(text=(detail if step == "present" else STEP_LABELS[step] + "…"),
                                      text_color=theme.ACCENT)
                if step == "present":
                    self._cancel.configure(state="normal")
                else:
                    self._cancel.configure(state="disabled")
            elif state == "done":
                icon.configure(text="✓", text_color=theme.OK)
                label.configure(text_color=theme.TEXT)
                if self._active == step:
                    self._active = None
            elif state == "skip":
                icon.configure(text="–", text_color=theme.MUTED)
            elif state == "fail":
                icon.configure(text="✗", text_color=theme.BAD)
                label.configure(text_color=theme.BAD)
                self._active = None
        elif kind == "done":
            self._finish(ev[1])
        elif kind == "error":
            self._fail(ev[1])

    # ---- outcomes ----------------------------------------------------------------------
    def _end(self) -> None:
        self._busy = False
        self._cancel.destroy()
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _finish(self, result: EnrolResult) -> None:
        self._end()
        self.result = result
        locked_ok = result.locked
        lock_failed = bool(result.lock_error)
        if lock_failed:
            self.banner.configure(text="Tag written and saved — but NOT locked", text_color=theme.WARN, fg_color=theme.WARN_SOFT)
            self.detail.configure(text=f"{result.lock_error}\nThe tag works normally; you can try locking it again.")
        else:
            self.banner.configure(text="✓ Tag written" + (" and locked" if locked_ok else ""), text_color=theme.OK, fg_color=theme.OK_SOFT)
            self.detail.configure(text="The record is saved on this device and is being sent to the server. "
                                       "Stick the tag on the asset.")
        self.app.refresh_status()
        if lock_failed:
            ctk.CTkButton(self.buttons, text="Try locking again", height=48, fg_color=theme.WARN_SOFT, text_color=theme.WARN,
                          hover_color=theme.WARN_SOFT, command=self._retry_lock).pack(side="left")
        ctk.CTkButton(self.buttons, text="Write another tag", height=48, fg_color=theme.ACCENT, text_color=theme.ACCENT_INK,
                      hover_color=theme.ACCENT_HOVER, font=theme.font(17, "bold"), command=lambda: self.close(again=True)).pack(side="right")
        ctk.CTkButton(self.buttons, text="Done", height=48, fg_color=theme.SURFACE_2, text_color=theme.TEXT,
                      hover_color=theme.LINE, command=self.close).pack(side="right", padx=8)

    def _fail(self, exc: EnrolError) -> None:
        self._end()
        cancelled = "Cancelled" in exc.message
        self.banner.configure(text="Cancelled" if cancelled else "Could not write the tag",
                              text_color=theme.WARN if cancelled else theme.BAD,
                              fg_color=theme.WARN_SOFT if cancelled else theme.BAD_SOFT)
        self.detail.configure(text="" if cancelled else
                              exc.message + "\nNo record was created for this tag." +
                              (f"\nTechnical: {exc.technical}" if exc.technical else ""))
        ctk.CTkButton(self.buttons, text="Back to the form", height=48, fg_color=theme.ACCENT, text_color=theme.ACCENT_INK,
                      hover_color=theme.ACCENT_HOVER, font=theme.font(17, "bold"), command=self.close).pack(side="right")

    def _retry_lock(self) -> None:
        for child in self.buttons.winfo_children():
            child.configure(state="disabled")
        self.banner.configure(text="Hold the SAME tag on the reader…", text_color=theme.ACCENT, fg_color=theme.SURFACE)

        def work() -> None:
            try:
                res = self.app.service.retry_lock(self.result, lambda *a: None)
                self.app.post(lambda: self._redraw(res))
            except Exception as exc:  # noqa: BLE001
                self.app.post(lambda: self._redraw(self.result, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _redraw(self, result: EnrolResult, error: str | None = None) -> None:
        for child in self.buttons.winfo_children():
            child.destroy()
        if error:
            result.lock_error = error
        self._finish(result)

    def close(self, again: bool = False) -> None:
        self.grab_release()
        self.destroy()
        self.app.after_write(again)


class App(ctk.CTk):
    def __init__(self, cfg, outbox: Outbox, drainer, reader_factory, *, size: str | None = None, fullscreen: bool = False):
        theme.setup()
        super().__init__()
        self.cfg, self.outbox, self.drainer = cfg, outbox, drainer
        self.reader_factory = reader_factory
        self.gate = PinGate(cfg.home)
        self.reader: Reader | None = None
        self.service: EnrolService | None = None
        self.reader_error = ""
        self.photo_path: str | None = None
        self._photo_ref = None
        self.last_activity = time.time()
        self.writing = False

        self.title(f"Asset Enrolment {__version__}")
        self.configure(fg_color=theme.BG)
        self.geometry(size or "1024x600")
        self.minsize(780, 460)
        if fullscreen or (size is None and self.winfo_screenheight() <= 600):
            self.attributes("-fullscreen", True)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.content = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=0)
        self.content.grid(row=0, column=0, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.kb_host = ctk.CTkFrame(self)
        self.kb_host.grid(row=1, column=0, sticky="ew")
        self.kb_host.grid_remove()
        self.keyboard = KeyboardManager(self.kb_host, on_change=self._keyboard_changed)

        self.pin_screen = PinScreen(self)
        self.main_screen = self._build_main()
        self._show(self.pin_screen)
        for seq in ("<Any-ButtonPress>", "<Any-KeyPress>", "<Motion>"):
            self.bind_all(seq, self._touch, add="+")
        self._ui_q: queue.Queue = queue.Queue()
        self.after(100, self._pump_ui)
        self.after(10_000, self._idle_check)
        self.after(2500, self._tick)
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        threading.Thread(target=self._open_reader, daemon=True).start()

    # ---------------------------------------------------------------- plumbing ------
    def logical_size(self) -> tuple[int, int]:
        """Window size in unscaled units (what the layout was designed in), whatever the HiDPI scale."""
        scale = self._get_window_scaling()
        return int(self.winfo_width() / scale), int(self.winfo_height() / scale)

    def post(self, fn) -> None:
        """Run `fn` on the UI thread. Tk is not thread-safe: worker threads must never call it."""
        self._ui_q.put(fn)

    def _pump_ui(self) -> None:
        try:
            while True:
                self._ui_q.get_nowait()()
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001
            log.exception("UI callback failed")
        self.after(100, self._pump_ui)

    def _touch(self, _e=None) -> None:
        self.last_activity = time.time()

    def _show(self, screen) -> None:
        for s in (self.pin_screen, self.main_screen):
            s.grid_forget()
        screen.grid(row=0, column=0, sticky="nsew")

    def unlocked(self) -> None:
        self._show(self.main_screen)
        self.refresh_status()

    def lock(self) -> None:
        if self.writing:
            return
        self.keyboard.hide()
        self.pin_screen.refresh()
        self._show(self.pin_screen)

    def _idle_check(self) -> None:
        if not self.writing and self.main_screen.winfo_ismapped() and time.time() - self.last_activity > IDLE_LOCK_SECONDS:
            self.lock()
        self.after(10_000, self._idle_check)

    def _tick(self) -> None:
        self.refresh_status()
        self.after(3000, self._tick)

    def _open_reader(self) -> None:
        try:
            reader = self.reader_factory()
            self.reader, self.reader_error = reader, ""
            self.service = EnrolService(self.cfg, reader, self.outbox, on_queued=self.drainer.kick)
        except Exception as exc:  # noqa: BLE001
            log.exception("reader unavailable")
            self.reader, self.service = None, None
            self.reader_error = str(exc)
        self.post(self.refresh_status)

    def quit_app(self) -> None:
        try:
            self.drainer.stop()
            if self.reader:
                self.reader.close()
        finally:
            self.destroy()

    def _keyboard_changed(self) -> None:
        if not hasattr(self, "write_btn"):
            return
        typing = self.keyboard.target is not None and self.logical_size()[1] < 700
        # Small screen while typing: hand every spare pixel to the three fields.
        for widget in (self.bar, self.right_panel, self.photo_row, self.lock_row, self.err, self.write_btn):
            if widget is None:
                continue
            (widget.grid_remove if typing else widget.grid)()
        self.left_panel.grid_configure(columnspan=2 if typing else 1, padx=10 if typing else (10, 5))

    # ---------------------------------------------------------------- main screen ------
    def _build_main(self) -> ctk.CTkFrame:
        screen = ctk.CTkFrame(self.content, fg_color=theme.BG)
        screen.grid_rowconfigure(1, weight=1)
        screen.grid_columnconfigure(0, weight=3)
        screen.grid_columnconfigure(1, weight=2)

        bar = ctk.CTkFrame(screen, fg_color=theme.SURFACE, corner_radius=0, height=48)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.bar = bar
        ctk.CTkLabel(bar, text="Asset Enrolment", font=theme.font(18, "bold"), text_color=theme.TEXT).pack(side="left", padx=14, pady=8)
        ctk.CTkButton(bar, text="Lock", width=64, height=34, fg_color=theme.SURFACE_2, text_color=theme.TEXT,
                      hover_color=theme.LINE, command=self.lock).pack(side="right", padx=(4, 10))
        ctk.CTkButton(bar, text="⚙", width=38, height=34, fg_color=theme.SURFACE_2, text_color=theme.TEXT,
                      hover_color=theme.LINE, command=self._settings).pack(side="right", padx=4)
        self.chips = ctk.CTkFrame(bar, fg_color="transparent")
        self.chips.pack(side="right", padx=6)

        # --- left: the form. Everything fits one screen, nothing scrolls. -----------------
        left = ctk.CTkFrame(screen, fg_color=theme.SURFACE, corner_radius=14, border_width=1, border_color=theme.LINE)
        left.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=8)
        self.left_panel = left
        left.grid_columnconfigure((0, 1), weight=1, uniform="cols")
        left.grid_rowconfigure(6, weight=1)

        def field(row: int, col: int, span: int, label: str, placeholder: str):
            left_pad = 14 if col == 0 else 6
            right_pad = 14 if col + span == 2 else 6
            ctk.CTkLabel(left, text=label, font=theme.font(13, "bold"), text_color=theme.TEXT, anchor="w").grid(
                row=row, column=col, columnspan=span, sticky="ew", padx=(left_pad, right_pad), pady=(10 if row == 0 else 4, 1))
            entry = ctk.CTkEntry(left, placeholder_text=placeholder, height=50, font=theme.font(17), corner_radius=10,
                                 fg_color=theme.SURFACE_2, border_color=theme.LINE, text_color=theme.TEXT,
                                 placeholder_text_color=theme.MUTED)
            entry.grid(row=row + 1, column=col, columnspan=span, sticky="ew", padx=(left_pad, right_pad))
            return entry

        self.f_name = field(0, 0, 2, "Asset name", "e.g. Epson projector")
        self.f_product = field(2, 0, 1, "Product ID", "e.g. PRJ-0042")
        self.f_room = field(2, 1, 1, "Room number", "e.g. LH-101")
        # "Next" walks name -> product ID -> room, so the form can be filled without closing the keyboard.
        self.keyboard.register(self.f_name, kind="name", autocap=True, next_to=self.f_product)
        self.keyboard.register(self.f_product, kind="product", upper=True, next_to=self.f_room)
        self.keyboard.register(self.f_room, kind="room", upper=True)

        self.photo_row = ctk.CTkFrame(left, fg_color="transparent")
        self.photo_row.grid(row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 0))
        self.photo_label = ctk.CTkLabel(self.photo_row, text="No photo", width=56, height=42, fg_color=theme.SURFACE_2,
                                        corner_radius=8, text_color=theme.MUTED, font=theme.font(10))
        self.photo_label.pack(side="left")
        ctk.CTkButton(self.photo_row, text="Add photo (optional)", height=42, fg_color=theme.SURFACE_2, text_color=theme.TEXT,
                      hover_color=theme.LINE, border_width=1, border_color=theme.LINE,
                      command=self._pick_photo).pack(side="left", padx=8)
        self.photo_remove = ctk.CTkButton(self.photo_row, text="Remove", height=42, width=70, fg_color=theme.SURFACE_2,
                                          text_color=theme.TEXT, hover_color=theme.LINE, command=self._clear_photo, state="disabled")
        self.photo_remove.pack(side="left")

        self.lock_var = tk.BooleanVar(value=False)
        self.lock_row = None
        if self.cfg.tag_lock_enabled:
            self.lock_row = ctk.CTkFrame(left, fg_color=theme.WARN_SOFT, corner_radius=10)
            self.lock_row.grid(row=5, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 0))
            ctk.CTkSwitch(self.lock_row, text="Lock this tag permanently (cannot be undone)", variable=self.lock_var,
                          font=theme.font(13, "bold"), text_color=theme.WARN).pack(anchor="w", padx=12, pady=8)

        self.err = ctk.CTkLabel(left, text="", text_color=theme.BAD, font=theme.font(13), anchor="nw", wraplength=430, justify="left")
        self.err.grid(row=6, column=0, columnspan=2, sticky="new", padx=16, pady=(6, 0))
        self.write_btn = ctk.CTkButton(left, text="Write tag", height=54, font=theme.font(20, "bold"), corner_radius=14,
                                       fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color=theme.ACCENT_INK,
                                       command=self._start_write)
        self.write_btn.grid(row=7, column=0, columnspan=2, sticky="ew", padx=14, pady=(6, 12))

        # --- right: recent tags (the last few; no scrolling) -----------------------------------
        right = ctk.CTkFrame(screen, fg_color=theme.SURFACE, corner_radius=14, border_width=1, border_color=theme.LINE)
        right.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=8)
        self.right_panel = right
        head = ctk.CTkFrame(right, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 2))
        ctk.CTkLabel(head, text="Recent tags", font=theme.font(16, "bold"), text_color=theme.TEXT).pack(side="left")
        ctk.CTkButton(head, text="Sync", width=60, height=30, fg_color=theme.SURFACE_2, text_color=theme.TEXT,
                      hover_color=theme.LINE, command=self._sync_now).pack(side="right")
        self.recent = ctk.CTkFrame(right, fg_color="transparent")
        self.recent.pack(fill="both", expand=True, padx=4, pady=(0, 6))
        return screen

    # ---- photo ---------------------------------------------------------------------------
    def _pick_photo(self) -> None:
        self.keyboard.hide()
        ImagePicker(self, self._photo_chosen)

    def _photo_chosen(self, path: str) -> None:
        try:
            thumb = images.preview(path, (56, 42))
        except images.ImageError as exc:
            self.err.configure(text=str(exc))
            return
        self.photo_path = path
        self._photo_ref = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=thumb.size)
        self.photo_label.configure(image=self._photo_ref, text="")
        self.photo_remove.configure(state="normal")

    def _clear_photo(self) -> None:
        self.photo_path, self._photo_ref = None, None
        self.photo_label.configure(image=None, text="No photo")
        self.photo_remove.configure(state="disabled")

    # ---- status chips + recent list -------------------------------------------------------
    def refresh_status(self) -> None:
        if not self.main_screen.winfo_ismapped():
            return
        for child in self.chips.winfo_children():
            child.destroy()
        chip(self.chips, "Reader ready" if self.reader else "Reader not ready",
             "ok" if self.reader else "bad", None if self.reader else self._reader_help).pack(side="left", padx=2)
        online = self.drainer.online
        chip(self.chips, {True: "Online", False: "Offline", None: "Connecting"}[online],
             {True: "ok", False: "warn", None: "neutral"}[online]).pack(side="left", padx=2)
        stats = self.outbox.stats()
        if stats["failed"]:
            chip(self.chips, f"{stats['failed']} rejected", "bad", self._show_failures).pack(side="left", padx=2)
        if stats["pending"]:
            chip(self.chips, f"{stats['pending']} unsent", "warn").pack(side="left", padx=2)
        drift = self.drainer.clock_drift
        if drift is not None and abs(drift) > 30:
            chip(self.chips, f"Clock off {int(abs(drift))}s", "bad").pack(side="left", padx=2)
        if self.cfg.tag_lock_enabled:
            chip(self.chips, "Tag lock ON", "warn").pack(side="left", padx=2)
        self.write_btn.configure(state="normal" if self.service and not self.writing else "disabled")
        self._render_recent()

    def _render_recent(self) -> None:
        rows = self.outbox.recent(4 if self.winfo_screenheight() < 700 else 8)
        sig = [(r["id"], r["state"], r["attempts"]) for r in rows]
        if getattr(self, "_recent_sig", None) == sig:
            return
        self._recent_sig = sig
        for child in self.recent.winfo_children():
            child.destroy()
        if not rows:
            ctk.CTkLabel(self.recent, text="Tags you write\nappear here.", text_color=theme.MUTED, font=theme.font(14)).pack(pady=30)
        for r in rows:
            card = ctk.CTkFrame(self.recent, fg_color=theme.SURFACE_2, corner_radius=10)
            card.pack(fill="x", padx=4, pady=2)
            icon, color = {"sent": ("✓", theme.OK), "pending": ("⏳", theme.WARN), "failed": ("✗", theme.BAD)}[r["state"]]
            ctk.CTkLabel(card, text=icon, width=30, font=theme.font(18, "bold"), text_color=color).pack(side="left", padx=(8, 0))
            col = ctk.CTkFrame(card, fg_color="transparent")
            col.pack(side="left", fill="x", expand=True, padx=6, pady=6)
            ctk.CTkLabel(col, text=r["name"], font=theme.font(14, "bold"), text_color=theme.TEXT, anchor="w", wraplength=200, justify="left").pack(fill="x")
            sub = f"Room {r['room_no']} · " + {"sent": "saved on server", "pending": "waiting to sync", "failed": "rejected"}[r["state"]]
            ctk.CTkLabel(col, text=sub, font=theme.font(11), text_color=theme.MUTED, anchor="w").pack(fill="x")
            if r["state"] == "failed" and r["last_error"]:
                ctk.CTkLabel(col, text=r["last_error"], font=theme.font(12), text_color=theme.BAD, anchor="w", wraplength=200, justify="left").pack(fill="x")

    def _sync_now(self) -> None:
        self.drainer.kick()
        threading.Thread(target=self.drainer.check_clock, daemon=True).start()
        self.after(1200, self.refresh_status)

    def _reader_help(self) -> None:
        messagebox.showinfo("Reader not ready", (self.reader_error or "The NFC reader has not started.") +
                            "\n\nCheck the wiring, then restart the app.")

    def _show_failures(self) -> None:
        bad = [r for r in self.outbox.recent(50) if r["state"] == "failed"]
        text = "\n\n".join(f"{r['name']} (room {r['room_no']}):\n{r['last_error']}" for r in bad) or "None."
        if messagebox.askyesno("Rejected by the server", text + "\n\nRetry them now?"):
            self.outbox.requeue_failed()
            self.drainer.kick()
            self.refresh_status()

    # ---- write flow ----------------------------------------------------------------------------
    def _start_write(self) -> None:
        self.keyboard.hide()
        self.err.configure(text="")
        try:
            name = validators.asset_name(self.f_name.get())
            pid = validators.product_id(self.f_product.get())
            room = validators.room_no(self.f_room.get())
        except validators.ValidationError as exc:
            labels = {"name": "Asset name", "product_id": "Product ID", "room_no": "Room number"}
            self.err.configure(text=f"{labels.get(exc.field, exc.field)}: {exc.message}")
            return
        if self.service is None:
            self._reader_help()
            return
        lock = bool(self.lock_var.get()) and self.cfg.tag_lock_enabled
        if lock and not self._confirm_lock():
            return
        self.writing = True
        self.write_btn.configure(state="disabled")
        WriteDialog(self, EnrolForm(name, pid, room, self.photo_path, lock), lock)

    def _confirm_lock(self) -> bool:
        win = ctk.CTkToplevel(self)
        win.title("Lock this tag?")
        win.geometry(f"560x300+{self.winfo_rootx() + 60}+{self.winfo_rooty() + 60}")
        win.configure(fg_color=theme.BG)
        win.transient(self)
        win.after(150, win.grab_set)
        answer = {"ok": False}
        ctk.CTkLabel(win, text="Lock this tag permanently?", font=theme.font(22, "bold"), text_color=theme.WARN).pack(pady=(22, 6))
        ctk.CTkLabel(win, text="Once locked, this tag can NEVER be rewritten, corrected or reused.\n"
                               "Check the name, product ID and room first.", font=theme.font(16), text_color=theme.TEXT,
                     justify="center", wraplength=500).pack(pady=6)
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(side="bottom", pady=22)
        ctk.CTkButton(row, text="Cancel", height=52, width=160, fg_color=theme.SURFACE_2, text_color=theme.TEXT,
                      hover_color=theme.LINE, command=win.destroy).pack(side="left", padx=8)
        go = ctk.CTkButton(row, text="Lock it (3)", height=52, width=200, fg_color=theme.BAD, hover_color=theme.BAD,
                           text_color=theme.ACCENT_INK, state="disabled")
        go.pack(side="left", padx=8)

        def accept() -> None:
            answer["ok"] = True
            win.destroy()
        go.configure(command=accept)

        def countdown(n: int) -> None:
            if not win.winfo_exists():
                return
            if n > 0:
                go.configure(text=f"Lock it ({n})")
                win.after(1000, lambda: countdown(n - 1))
            else:
                go.configure(text="Yes, lock it", state="normal")
        countdown(3)
        self.wait_window(win)
        return answer["ok"]

    def after_write(self, again: bool) -> None:
        self.writing = False
        self.refresh_status()
        if again:
            self.f_name.delete(0, "end")
            self.f_product.delete(0, "end")
            self._clear_photo()          # the room number stays: tags are usually written room by room
            self.f_name.focus()
        else:
            self.focus_set()

    # ---- settings ----------------------------------------------------------------------------------
    def _settings(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("About this device")
        win.geometry(f"560x400+{self.winfo_rootx() + 60}+{self.winfo_rooty() + 10}")
        win.configure(fg_color=theme.BG)
        win.transient(self)
        win.after(150, win.grab_set)
        ctk.CTkLabel(win, text="About this device", font=theme.font(22, "bold"), text_color=theme.TEXT).pack(pady=(18, 8))
        info = [("Version", __version__), ("Device ID", self.cfg.device_id[:13] + "…"), ("Server", self.cfg.backend_url),
                ("Tag host", self.cfg.public_host), ("Reader", f"{self.cfg.reader} / {self.cfg.pn532_interface}"),
                ("Tag locking", "ENABLED" if self.cfg.tag_lock_enabled else "off (tags stay rewritable)"),
                ("Clone tags", "accepted (no locking)" if self.cfg.allow_clone_tags else "refused"),
                ("Log file", str(self.cfg.log_path))]
        box = ctk.CTkFrame(win, fg_color=theme.SURFACE, corner_radius=12)
        box.pack(fill="x", padx=20)
        for k, v in info:
            r = ctk.CTkFrame(box, fg_color="transparent")
            r.pack(fill="x", padx=12, pady=3)
            ctk.CTkLabel(r, text=k, width=110, anchor="w", text_color=theme.MUTED).pack(side="left")
            ctk.CTkLabel(r, text=v, anchor="w", text_color=theme.TEXT, wraplength=360, justify="left").pack(side="left")
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(side="bottom", pady=18)
        ctk.CTkButton(row, text="Quit app", fg_color=theme.BAD_SOFT, text_color=theme.BAD, hover_color=theme.BAD_SOFT,
                      height=44, command=lambda: (win.destroy(), self.quit_app())).pack(side="left", padx=8)
        ctk.CTkButton(row, text="Close", height=44, fg_color=theme.SURFACE_2, text_color=theme.TEXT, hover_color=theme.LINE,
                      command=win.destroy).pack(side="left", padx=8)
