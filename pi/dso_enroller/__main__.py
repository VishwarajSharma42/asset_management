"""python -m dso_enroller [--sim] [--size WxH] [--fullscreen] [--set-pin] [--check-config]"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys

from . import __version__


def _logging(path) -> None:
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dso-enroller", description="Write DSO asset tags")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--sim", action="store_true", help="use the built-in tag simulator (no hardware)")
    ap.add_argument("--size", help="window size, e.g. 800x480")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--set-pin", action="store_true", help="set or change the operator PIN in the terminal, then exit")
    ap.add_argument("--check-config", action="store_true", help="validate configuration and exit")
    args = ap.parse_args(argv)

    from . import config as cfgmod
    try:
        cfg = cfgmod.load()
    except cfgmod.ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 2
    if args.sim:
        from dataclasses import replace
        cfg = replace(cfg, reader="sim")
    _logging(cfg.log_path)

    if args.check_config:
        print(f"OK — device {cfg.device_id}, server {cfg.backend_url}, tag host {cfg.public_host}, "
              f"reader {cfg.reader}, locking {'ON' if cfg.tag_lock_enabled else 'off'}")
        return 0

    from .security import PinGate, PinPolicyError
    if args.set_pin:
        import getpass
        gate = PinGate(cfg.home)
        pin = getpass.getpass("New PIN (6-12 digits): ")
        if pin != getpass.getpass("Again: "):
            print("The PINs differ.", file=sys.stderr)
            return 1
        try:
            gate.set_pin(pin)
        except PinPolicyError as exc:
            print(exc, file=sys.stderr)
            return 1
        print("PIN saved.")
        return 0

    from .drainer import Drainer
    from .outbox import Outbox
    from .reader import open_reader
    from .ui.app import App

    outbox = Outbox(cfg.outbox_path)
    app_ref: dict = {}
    drainer = Drainer(cfg, outbox, on_change=lambda: app_ref.get("app") and app_ref["app"].post(app_ref["app"].refresh_status))
    drainer.start()
    drainer.kick()
    import threading
    threading.Thread(target=drainer.check_clock, daemon=True).start()

    app = App(cfg, outbox, drainer, lambda: open_reader(cfg), size=args.size, fullscreen=args.fullscreen)
    app_ref["app"] = app
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
