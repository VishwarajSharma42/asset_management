"""Pi -> outbox -> drainer -> backend -> public tap, with both real code bases.

Nothing between the two is mocked except the TCP socket: the drainer's `requests` session
is swapped for one that calls the Flask test client, so the request that arrives is the
one the Pi really built and signed.
"""
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi"))
sys.path.insert(0, str(ROOT / "backend"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


backend_helpers = _load("backend_helpers", ROOT / "backend" / "tests" / "conftest.py")
pi_helpers = _load("pi_helpers", ROOT / "pi" / "tests" / "conftest.py")

from dso_enroller.drainer import Drainer  # noqa: E402
from dso_enroller.outbox import Outbox  # noqa: E402
from dso_enroller.reader import SimulatedReader  # noqa: E402
from dso_enroller.service import EnrolForm, EnrolService  # noqa: E402
from dsoreg.db import assets, usage_log  # noqa: E402


class FlaskSession:
    """Stands in for requests.Session: same call shape, no socket."""
    def __init__(self, client, base):
        self.client, self.base = client, base
        self.fail_next = 0
        self.lose_response = 0

    def post(self, url, data=None, headers=None, timeout=None, verify=None):
        if self.fail_next:
            self.fail_next -= 1
            raise requests.ConnectionError("network down")
        r = self.client.post(url.replace(self.base, ""), data=data, headers=headers)
        if self.lose_response:                       # server processed it; the reply never arrived
            self.lose_response -= 1
            raise requests.Timeout("reply lost")
        return type("Resp", (), {"status_code": r.status_code, "json": lambda self_: r.get_json()})()


@pytest.fixture()
def system(tmp_path):
    (tmp_path / "srv").mkdir()
    (tmp_path / "pi").mkdir()
    env = backend_helpers.Env(tmp_path / "srv")
    cfg = pi_helpers.make_config(
        tmp_path / "pi", device_id=env.device_id, device_key=env.pi.key,
        field_recipient_pub=env.settings.field_recipient_pub, tag_key=env.settings.tag_key,
        public_host=env.settings.public_host)
    box = Outbox(tmp_path / "pi" / "outbox.db")
    reader = SimulatedReader(tag_delay=0.0, auto_new_tag=True)
    svc = EnrolService(cfg, reader, box)
    drainer = Drainer(cfg, box)
    drainer._session = FlaskSession(env.client, cfg.backend_url)
    yield type("S", (), {"env": env, "cfg": cfg, "box": box, "reader": reader, "svc": svc, "drainer": drainer})
    box.close()


def tap(env, link):
    q = parse_qs(urlsplit(link).query)
    return env.client.post("/api/v1/scan", json={"t": q["t"][0], "d": q["d"][0]}).get_json()


def test_full_lifecycle(system):
    s = system
    # 1. admin enrols a scope on the Pi, with a photo
    img = Path(s.cfg.home) / "photo.png"
    from PIL import Image
    Image.new("RGB", (800, 600), (30, 90, 160)).save(img)
    res = s.svc.enrol(EnrolForm("Tektronix TBS1052C", "DSO-EXTC-0042", "EXTC-3", image_path=str(img)), lambda *a: None)
    assert s.box.stats()["pending"] == 1
    assert s.drainer.drain_once() == 1 and s.box.stats() == {"pending": 0, "failed": 0, "sent": 1}

    # 2. a student taps the tag
    passport = tap(s.env, res.link)
    assert passport["verdict"] == "ok"
    assert (passport["asset"]["name"], passport["asset"]["room_no"]) == ("Tektronix TBS1052C", "EXTC-3")
    assert s.env.client.get(passport["asset"]["image_url"]).status_code == 200

    assert s.env.client.get("/health").get_json()["status"] == "ok"


def _iso(minutes):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_students_use_it_and_staff_see_it_on_the_dashboard(system):
    s, env = system, system.env
    res = s.svc.enrol(EnrolForm("Rigol DS1054Z", "DSO-EXTC-0043", "EXTC-4"), lambda *a: None)
    s.drainer.drain_once()

    for who, ok, note in (("Asha Patil", True, None), ("Rohan Mehta", False, "Probe 2 is not reading")):
        ticket = tap(env, res.link)["ticket"]
        body = {"ticket": ticket, "user_name": who, "start_at": _iso(-40), "end_at": _iso(20), "working": ok}
        if note:
            body["fault_note"] = note
        assert env.client.post("/api/v1/usage", json=body).status_code == 201

    admin = env.login()
    row = env.client.get("/api/v1/assets", headers=admin).get_json()["items"][0]
    assert row["product_id"] == "DSO-EXTC-0043"
    assert row["condition"] == "faulty" and row["in_use_count"] == 2 and row["open_faults"] == 1
    usage = env.client.get(f"/api/v1/assets/{row['id']}/usage", headers=admin).get_json()["items"]
    assert {u["user_name"]: u["overlaps"] for u in usage} == {"Asha Patil": 1, "Rohan Mehta": 1}
    assert env.client.get("/api/v1/audit/verify", headers=admin).get_json()["ok"]


def test_offline_enrolments_arrive_exactly_once_after_reconnect(system):
    s, env = system, system.env
    links = []
    for i in range(5):
        links.append(s.svc.enrol(EnrolForm(f"Scope {i}x", f"OFF-{i}", "EXTC-1"), lambda *a: None).link)
    s.drainer._session.fail_next = 5
    assert s.drainer.drain_once() == 0                        # network is down
    assert s.box.stats()["pending"] == 5 and s.box.stats()["failed"] == 0
    for row in s.box.due(now=10**12):                          # skip the backoff wait
        pass
    s.box._db.execute("UPDATE outbox SET next_attempt=0")
    assert s.drainer.drain_once() == 5
    with env.engine.connect() as c:
        assert len(c.execute(select(assets)).all()) == 5
    assert all(tap(env, link)["verdict"] == "ok" for link in links)


def test_lost_reply_does_not_create_a_duplicate(system):
    s, env = system, system.env
    res = s.svc.enrol(EnrolForm("Keysight X", "LOST-1", "EXTC-1"), lambda *a: None)
    s.drainer._session.lose_response = 1
    assert s.drainer.drain_once() == 0                        # server saved it, the Pi never heard
    s.box._db.execute("UPDATE outbox SET next_attempt=0")
    assert s.drainer.drain_once() == 1                        # same idempotency key: stored reply returned
    with env.engine.connect() as c:
        assert len(c.execute(select(assets)).all()) == 1
    assert tap(env, res.link)["verdict"] == "ok"


def test_server_rejection_is_surfaced_not_retried_forever(system):
    s = system
    s.svc.enrol(EnrolForm("Scope One", "DUP-1", "EXTC-1"), lambda *a: None)
    s.svc.enrol(EnrolForm("Scope Two", "DUP-1", "EXTC-2"), lambda *a: None)   # same product ID, different tag
    s.drainer.drain_once()
    st = s.box.stats()
    assert st["sent"] == 1 and st["failed"] == 1
    failed = [r for r in s.box.recent() if r["state"] == "failed"][0]
    assert "already registered" in failed["last_error"]


def test_revoked_device_stops_enrolling_immediately(system):
    from sqlalchemy import update
    from dsoreg.db import devices
    s = system
    s.svc.enrol(EnrolForm("Scope One", "REV-1", "EXTC-1"), lambda *a: None)
    with s.env.engine.begin() as c:
        c.execute(update(devices).values(status="revoked"))
    s.drainer.drain_once()
    assert s.box.stats()["failed"] == 1 and "revoked" in s.box.recent()[0]["last_error"]


def test_a_cloned_tag_with_swapped_data_is_caught_at_the_tap(system):
    s, env = system, system.env
    a = s.svc.enrol(EnrolForm("Scope A", "SWAP-A", "EXTC-1"), lambda *a: None)
    b = s.svc.enrol(EnrolForm("Scope B", "SWAP-B", "EXTC-2"), lambda *a: None)
    s.drainer.drain_once()
    ta, db = parse_qs(urlsplit(a.link).query)["t"][0], parse_qs(urlsplit(b.link).query)["d"][0]
    assert env.client.post("/api/v1/scan", json={"t": ta, "d": db}).get_json()["verdict"] == "tag_mismatch"
