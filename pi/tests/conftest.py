import sys
from pathlib import Path

PI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PI))

import os  # noqa: E402

import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402

from dso_enroller.config import Config  # noqa: E402
from dso_enroller.outbox import Outbox  # noqa: E402
from dso_enroller.reader import SimulatedReader  # noqa: E402
from dso_enroller.service import EnrolService  # noqa: E402


def make_config(tmp_path, **over) -> Config:
    base = dict(
        home=tmp_path, backend_url="https://assets.test", public_host="assets.test",
        device_id="11111111-1111-1111-1111-111111111111",
        field_recipient_pub=X25519PrivateKey.generate().public_key().public_bytes_raw(),
        device_key=Ed25519PrivateKey.generate(), tag_key=os.urandom(16), lock_key=None,
        tag_lock_enabled=False, reader="sim", pn532_interface="i2c", allow_insecure_http=False,
        page_write_delay=0.0, page_write_retries=3, write_mode="library", ca_bundle=None,
        outbox_path=tmp_path / "outbox.db", log_path=tmp_path / "log.txt")
    base.update(over)
    return Config(**base)


@pytest.fixture()
def rig(tmp_path):
    cfg = make_config(tmp_path)
    reader = SimulatedReader(tag_delay=0.0)
    box = Outbox(cfg.outbox_path)
    events = []
    svc = EnrolService(cfg, reader, box)
    yield type("Rig", (), {"cfg": cfg, "reader": reader, "box": box, "svc": svc, "events": events,
                           "progress": staticmethod(lambda s, st, d="": events.append((s, st)))})
    box.close()
