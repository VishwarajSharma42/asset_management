"""Test fixtures. The suite talks to the real Flask app over its test client, with a real
database (SQLite) and real cryptography — nothing is mocked.

`PiSim` plays the Pi: it builds exactly the requests the Pi would, so the backend is
tested against the same wire format the device sends.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dsoreg.app import create_app  # noqa: E402
from dsoreg.config import Settings  # noqa: E402
from dsoreg.db import devices, make_engine, metadata, utcnow  # noqa: E402
from dsoreg.security import envelope, taglink  # noqa: E402
from dsoreg.security.keys import wrap  # noqa: E402
from dsoreg.security.signing import canonical_request  # noqa: E402
from dsoreg.security.util import b64u, sha256_hex  # noqa: E402
from dsoreg.services import users as users_svc  # noqa: E402

RAW_PRIV = dict(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption())
RAW_PUB = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
HOST = "assets.test"


def make_settings(tmp_path, **over) -> tuple[Settings, dict]:
    kek = os.urandom(32)
    fr, row, sess = X25519PrivateKey.generate(), Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    base = dict(
        env="testing", database_url=f"sqlite:///{tmp_path / 'test.db'}", kek=kek,
        field_recipient_key_wrapped=wrap(kek, "field_recipient", fr.private_bytes(**RAW_PRIV)),
        field_recipient_pub=fr.public_key().public_bytes(**RAW_PUB),
        row_signing_key_wrapped=wrap(kek, "row_signing", row.private_bytes(**RAW_PRIV)),
        row_signing_pubkey=row.public_key().public_bytes(**RAW_PUB),
        session_signing_key_wrapped=wrap(kek, "session_signing", sess.private_bytes(**RAW_PRIV)),
        session_signing_pubkey=sess.public_key().public_bytes(**RAW_PUB),
        ticket_key=os.urandom(32), tag_index_key=os.urandom(32), tag_key=os.urandom(16),
        ip_hash_seed=os.urandom(32), public_host=HOST, media_dir=str(tmp_path / "media"),
        frontend_dir=str(Path(__file__).resolve().parents[2] / "frontend"), serve_frontend=False)
    base.update(over)
    return Settings(**base), {"fr_pub": base["field_recipient_pub"]}


@dataclass
class PiSim:
    device_id: str
    key: Ed25519PrivateKey
    settings: Settings

    def link(self, product_id: str, host: str = HOST, scheme: str = "https"):
        token = os.urandom(16)
        d = taglink.seal_product_id(self.settings.tag_key, token, product_id)
        return token, f"{scheme}://{host}/c?t={b64u(token)}&d={d}"

    def payload(self, *, product_id="DSO-0001", name="Tektronix TBS1052C", room="EXTC-3",
                tag_uid="04A1B2C3D4E5F6", image=None, lock_requested=False, link=None, token=None,
                override=None) -> dict:
        if link is None:
            token, link = self.link(product_id)
        th = sha256_hex(token)
        sealed = envelope.seal(
            json.dumps({"product_id": product_id, "tag_uid": tag_uid, "link": link}).encode(),
            self.settings.field_recipient_pub, f"enrol|{self.device_id}|{th}".encode())
        body = {"schema": "dsoreg.enrol.v1", "name": name, "room_no": room, "token_hash": th,
                "sealed": b64u(sealed), "image": image, "lock_requested": lock_requested}
        body.update(override or {})
        return body

    def request(self, body: dict | bytes, *, ts: int | None = None, idem: str | None = None,
                sign_with: Ed25519PrivateKey | None = None, device_id: str | None = None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        ts = str(int(time.time()) if ts is None else ts)
        idem = idem or uuid.uuid4().hex
        msg = canonical_request("POST", "/api/v1/enrol", ts, idem, raw)
        sig = (sign_with or self.key).sign(msg).hex()
        headers = {"X-Device-Id": device_id or self.device_id, "X-Timestamp": ts,
                   "X-Idempotency-Key": idem, "X-Signature": sig, "Content-Type": "application/json"}
        return headers, raw


def png_b64(size=(64, 48), color=(200, 30, 30), exif=False) -> str:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    kwargs = {}
    if exif:
        e = Image.Exif()
        e[0x010F] = "SecretCameraMaker"
        kwargs["exif"] = e
    img.save(buf, format="JPEG" if exif else "PNG", **kwargs)
    return base64.b64encode(buf.getvalue()).decode()


class Env:
    def __init__(self, tmp_path, **over):
        self.settings, _ = make_settings(tmp_path, **over)
        self.engine = make_engine(self.settings.database_url)
        metadata.create_all(self.engine)
        self.app = create_app(self.settings, self.engine)
        self.client = self.app.test_client()
        self.ctx = self.app.extensions["dsoreg"]
        key = Ed25519PrivateKey.generate()
        self.device_id = str(uuid.uuid4())
        with self.engine.begin() as c:
            c.execute(devices.insert().values(
                device_id=self.device_id, label="pi-test", status="active", created_at=utcnow(),
                public_key=key.public_key().public_bytes(**RAW_PUB).hex()))
        self.pi = PiSim(self.device_id, key, self.settings)

    def enrol(self, **kw):
        headers, raw = self.pi.request(self.pi.payload(**kw))
        return self.client.post("/api/v1/enrol", data=raw, headers=headers)

    def enrol_ok(self, **kw) -> dict:
        r = self.enrol(**kw)
        assert r.status_code == 201, r.get_data(as_text=True)
        return r.get_json()

    def make_user(self, username="admin1", role="admin", password="correct horse battery"):
        users_svc.create_user(self.ctx, username=username, display_name=username.title(),
                              role=role, password=password, actor="test", now=utcnow())
        return username, password

    def login(self, username="admin1", role="admin", password="correct horse battery") -> dict:
        try:
            self.make_user(username, role, password)
        except Exception:
            pass
        r = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.get_data(as_text=True)
        return {"Authorization": "Bearer " + r.get_json()["token"]}

    def scan_for(self, link: str) -> dict:
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(link).query)
        return self.client.post("/api/v1/scan", json={"t": q["t"][0], "d": q["d"][0]}).get_json()


@pytest.fixture()
def env(tmp_path):
    return Env(tmp_path)


def iso_now(delta_minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)).isoformat()
