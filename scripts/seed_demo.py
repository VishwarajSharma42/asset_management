#!/usr/bin/env python3
"""DEVELOPMENT ONLY. Fills a dev database with demo assets, staff accounts and usage so
the dashboard has something to show. Refuses to run unless DSOREG_ENV=development.

    python scripts/seed_demo.py --env-file secrets.local/backend.env --pi-home secrets.local/pi

Everything goes through the real code paths: assets are enrolled by the Pi's own enrolment
service (simulated reader) and delivered by its real outbox + drainer; usage is submitted
through the public endpoints with real scan tickets.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "pi")]

from PIL import Image, ImageDraw  # noqa: E402


def load_env(path: str) -> None:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


def asset_image(path: Path, label: str, hue: tuple[int, int, int]) -> None:
    img = Image.new("RGB", (800, 600), (24, 30, 38))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 60, 740, 540), 28, fill=(48, 56, 66), outline=hue, width=6)
    d.rectangle((120, 110, 680, 430), fill=(8, 14, 12))
    for i in range(1, 8):
        d.line((120 + i * 70, 110, 120 + i * 70, 430), fill=(24, 48, 40))
    for j in range(1, 6):
        d.line((120, 110 + j * 53, 680, 110 + j * 53), fill=(24, 48, 40))
    pts = [(120 + x, 270 + int(math.sin(x / 38) * 90)) for x in range(0, 561, 4)]
    d.line(pts, fill=(80, 255, 150), width=4)
    for k in range(5):
        d.ellipse((160 + k * 100, 468, 196 + k * 100, 504), fill=hue)
    d.text((130, 120), label, fill=(120, 255, 190))
    img.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--pi-home", required=True)
    args = ap.parse_args()

    load_env(args.env_file)
    if os.environ.get("DSOREG_ENV") != "development":
        print("refusing: this script only runs against a development environment", file=sys.stderr)
        return 1
    os.environ["DSO_ENROLLER_HOME"] = str(Path(args.pi_home).resolve())
    os.environ["DSO_ENROLLER_STATE"] = str(Path(args.pi_home).resolve() / "state")

    from dso_enroller import config as pi_config
    from dso_enroller.drainer import Drainer
    from dso_enroller.outbox import Outbox
    from dso_enroller.reader import SimulatedReader
    from dso_enroller.service import EnrolForm, EnrolService
    from dsoreg.app import create_app
    from dsoreg.cli import main as cli_main
    from dsoreg.db import devices, utcnow
    from dsoreg.services import users as users_svc
    from sqlalchemy import select

    app = create_app()
    ctx = app.extensions["dsoreg"]
    client = app.test_client()
    cfg = pi_config.load()

    with ctx.engine.begin() as conn:
        if conn.execute(select(devices).where(devices.c.device_id == cfg.device_id)).first() is None:
            pub = cfg.device_key.public_key().public_bytes_raw().hex()
            conn.execute(devices.insert().values(device_id=cfg.device_id, label="pi-dev", public_key=pub,
                                                 status="active", created_at=utcnow()))
    for username, name, role, pw in (("admin", "Dr. Meera Kulkarni", "admin", "Demo-Passphrase-Alpha-2026"),
                                     ("viewer", "Lab Assistant", "viewer", "Demo-Passphrase-Bravo-2026")):
        try:
            users_svc.create_user(ctx, username=username, display_name=name, role=role, password=pw,
                                  actor="seed", now=utcnow())
        except Exception as exc:  # noqa: BLE001
            print(f"  user {username}: {getattr(exc, 'code', exc)}")  # e.g. already exists

    class Session:
        def post(self, url, data=None, headers=None, timeout=None, verify=None):
            r = client.post(url.replace(cfg.backend_url, ""), data=data, headers=headers)
            return type("R", (), {"status_code": r.status_code, "json": lambda s: r.get_json()})()

    tmp = Path(tempfile.mkdtemp())
    box = Outbox(tmp / "seed-outbox.db")
    svc = EnrolService(cfg, SimulatedReader(tag_delay=0.0, auto_new_tag=True), box)
    drainer = Drainer(cfg, box)
    drainer._session = Session()

    catalogue = [
        ("Epson EB-X06 Projector", "PRJ-0001", "LH-101", (60, 160, 255)),
        ("Dell Latitude 5420 Laptop", "LAP-0002", "LH-101", (255, 170, 60)),
        ("Tektronix TBS1052C Oscilloscope", "LAB-0003", "EXTC-4", (230, 80, 120)),
        ("HP LaserJet Pro M404 Printer", "PRN-0004", "ADM-12", (120, 220, 120)),
        ("Fluke 117 Multimeter", "LAB-0005", "EXTC-7", (180, 120, 255)),
        ("Canon EOS 250D Camera", "CAM-0006", "MED-2", (255, 220, 80)),
        ("Mobile Whiteboard", "FUR-0007", "LH-102", (90, 200, 200)),
        ("Cisco Catalyst 2960 Switch", "NET-0008", "SRV-1", (255, 120, 90)),
    ]
    results = []
    for i, (name, pid, room, hue) in enumerate(catalogue):
        photo = tmp / f"{pid}.png"
        if i != 6:  # one item has no photo, to show the empty state
            asset_image(photo, pid, hue)
        results.append(svc.enrol(EnrolForm(name, pid, room, str(photo) if i != 6 else None), lambda *a: None))
    drainer.drain_once()
    print("enrolled", box.stats())
    links = [r.link for r in results]

    counter = [0]

    def use(idx: int, who: str, start_min: int, end_min: int, working: bool = True, note: str | None = None) -> None:
        counter[0] += 1  # a different "phone" each time, so the demo does not trip the per-address rate limit
        addr = {"REMOTE_ADDR": f"10.1.{counter[0] // 250}.{counter[0] % 250 + 1}"}
        q = parse_qs(urlsplit(links[idx]).query)
        ticket = client.post("/api/v1/scan", json={"t": q["t"][0], "d": q["d"][0]},
                             environ_overrides=addr).get_json()["ticket"]
        now = datetime.now(timezone.utc)
        body = {"ticket": ticket, "user_name": who, "working": working,
                "start_at": (now + timedelta(minutes=start_min)).isoformat(),
                "end_at": (now + timedelta(minutes=end_min)).isoformat()}
        if note:
            body["fault_note"] = note
        n = counter[0]
        if n % 7 == 3:
            body["location_error"] = "denied"
        elif n % 7 != 5:
            body["location"] = {"lat": 19.0760 + (n % 9) * 0.00012, "lng": 72.8777 + (n % 11) * 0.00010,
                                "accuracy": 8.0 + (n % 5) * 6}
        r = client.post("/api/v1/usage", json=body, environ_overrides=addr)
        assert r.status_code == 201, r.get_json()

    day = 24 * 60
    # right now: two people share the projector, one person has the multimeter
    use(0, "Asha Patil", -25, 35)
    use(0, "Rohan Mehta", -10, 50)
    use(4, "Sara D'Souza", -40, 20)
    # earlier sessions, a few overlapping
    for who, a, b in (("Karan Shah", -day - 200, -day - 120), ("Neha Iyer", -day - 180, -day - 100),
                      ("Asha Patil", -2 * day - 300, -2 * day - 210), ("Imran Qureshi", -3 * day - 90, -3 * day - 30)):
        use(0, who, a, b)
    use(1, "Neha Iyer", -day + 60, -day + 150)
    use(1, "Karan Shah", -2 * day, -2 * day + 90)
    use(3, "Imran Qureshi", -day - 400, -day - 330)
    use(5, "Asha Patil", -2 * day - 60, -2 * day + 30)
    use(7, "Rohan Mehta", -day - 50, -day + 40)
    # faults: one open on #2, one open on #7, one raised and then fixed on #5
    use(2, "Sara D'Souza", -300, -240, working=False, note="Channel 2 shows no signal; probe tested on channel 1 and it works")
    use(7, "Karan Shah", -120, -70, working=False, note="Port 7 is not passing traffic; the link light stays off")
    use(5, "Imran Qureshi", -2 * day + 200, -2 * day + 260, working=False, note="Autofocus hunts and never locks")
    use(5, "Neha Iyer", -day + 30, -day + 100)

    from dsoreg.services import registry
    for f in registry.list_faults(ctx, "open"):
        if f["asset_name"].startswith("Canon"):
            registry.resolve_fault(ctx, f["id"], "Cleaned the lens contacts and updated the firmware", "user:admin", utcnow())

    print("demo data ready.  admin / Demo-Passphrase-Alpha-2026   viewer / Demo-Passphrase-Bravo-2026")
    print("tap URLs (open these on a phone or in the browser to see the public page):")
    for (name, *_), r in zip(catalogue, results):
        print(" ", name, "->", r.link)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
