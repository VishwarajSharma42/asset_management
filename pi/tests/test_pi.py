import json
import os
import stat
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from PIL import Image

from dso_enroller import config as cfgmod
from dso_enroller import crypto, images, layout, ntag, validators
from dso_enroller.outbox import Outbox
from dso_enroller.security import PinGate, PinLocked, PinPolicyError
from dso_enroller.service import EnrolError, EnrolForm

V = json.loads((Path(__file__).resolve().parents[2] / "vectors" / "crypto.json").read_text())


# ---- the cross-implementation contract ------------------------------------------------
def test_pi_reproduces_backend_vectors():
    e = V["envelope"]
    sealed = crypto.seal(bytes.fromhex(e["plaintext"]), bytes.fromhex(e["recipient_pub"]), bytes.fromhex(e["aad"]),
                         _eph=X25519PrivateKey.from_private_bytes(bytes.fromhex(e["ephemeral_priv"])),
                         _nonce=bytes.fromhex(e["nonce"]))
    assert sealed.hex() == e["sealed"]
    t = V["taglink"]
    assert crypto.seal_product_id(bytes.fromhex(t["tag_key"]), bytes.fromhex(t["token"]), t["product_id"],
                                  _nonce=bytes.fromhex(t["nonce"])) == t["d"]
    s = V["signing"]
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(s["device_priv"]))
    sig = crypto.sign_request(key, s["method"], s["path"], s["timestamp"], s["idem"], bytes.fromhex(s["body"]))
    assert sig == s["signature"]


# ---- tag layout ---------------------------------------------------------------------------
def test_ndef_tlv_is_well_formed_and_fits_one_frame():
    token = os.urandom(16)
    d = crypto.seal_product_id(os.urandom(16), token, "X" * 32)
    link = layout.build_link("assets.college.edu", token, d)
    tlv = layout.build_ndef_tlv(link)
    assert len(tlv) % 4 == 0 and len(tlv) <= layout.MAX_NDEF_BYTES
    assert tlv[0] == 0x03 and tlv[-4:].rstrip(b"\x00")[-1:] == b"\xfe" or b"\xfe" in tlv
    assert layout.decode_url(tlv) == link


def test_overlong_host_is_refused_not_truncated():
    token = os.urandom(16)
    d = crypto.seal_product_id(os.urandom(16), token, "P" * 32)
    with pytest.raises(layout.LayoutError):
        layout.build_ndef_tlv(layout.build_link("h" * 160 + ".example.com", token, d))
    for bad in ("evil.com/x", "a@b.com", "has space.com", ""):
        with pytest.raises(layout.LayoutError):
            layout.build_link(bad, token, d)


def test_dynamic_lock_bytes_cover_exactly_the_written_pages():
    assert ntag.dynamic_lock_bytes(0x0F) == [0, 0, 0]
    assert ntag.dynamic_lock_bytes(0x1F) == [0b1, 0, 0]
    assert ntag.dynamic_lock_bytes(0x2F) == [0b11, 0, 0]
    assert ntag.dynamic_lock_bytes(0xE1) == [0xFF, 0b00111111, 0]


# ---- validation ---------------------------------------------------------------------------
def test_validators_match_server_rules():
    assert validators.product_id(" DSO-0001 ") == "DSO-0001"
    assert validators.room_no("EXTC 3") == "EXTC 3"
    for bad in ("bad id", "x" * 33, "", "a;b", "<script>"):
        with pytest.raises(validators.ValidationError):
            validators.product_id(bad)
    with pytest.raises(validators.ValidationError):
        validators.asset_name("x")


# ---- the service ----------------------------------------------------------------------------
def test_enrol_writes_verifies_and_queues_before_returning(rig):
    res = rig.svc.enrol(EnrolForm("Tektronix TBS1052C", "DSO-0001", "EXTC-3"), rig.progress)
    steps = [s for s, st in rig.events if st == "done"]
    assert steps == ["validate", "present", "identify", "write", "verify", "queue"]
    assert rig.reader.verify_ndef(layout.build_ndef_tlv(res.link)) is None
    row = rig.box.due()[0]
    body = json.loads(bytes(row["body"]))
    assert body["schema"] == "dsoreg.enrol.v1" and body["name"] == "Tektronix TBS1052C"
    assert b"DSO-0001" not in bytes(row["body"])            # the product ID is sealed, not readable on disk
    assert body["lock_requested"] is False and res.locked is False


def test_failed_write_is_not_queued(rig):
    rig.reader.fail_writes = 99
    with pytest.raises(EnrolError) as exc:
        rig.svc.enrol(EnrolForm("Rigol DS1054Z", "DSO-0002", "EXTC-3"), rig.progress)
    assert exc.value.step == "write" and "page" in exc.value.message
    assert rig.box.stats()["pending"] == 0                    # no record for a tag that was never written
    assert ("write", "fail") in rig.events


def test_transient_write_errors_are_retried(rig):
    rig.reader.fail_writes = 2
    rig.svc.enrol(EnrolForm("Rigol DS1054Z", "DSO-0003", "EXTC-3"), rig.progress)
    assert rig.box.stats()["pending"] == 1


def test_bad_input_never_touches_the_tag(rig):
    with pytest.raises(EnrolError) as exc:
        rig.svc.enrol(EnrolForm("ok name", "bad id!", "EXTC-3"), rig.progress)
    assert exc.value.step == "validate" and rig.reader.write_log == []


def test_locking_is_off_unless_configured_and_requested(rig):
    res = rig.svc.enrol(EnrolForm("Rigol DS1054Z", "DSO-0004", "EXTC-3", lock=True), rig.progress)
    assert res.locked is False and not rig.reader.locked      # config says tag_lock_enabled = false


def test_lock_happens_after_the_record_is_durable_and_makes_the_tag_read_only(tmp_path):
    from conftest import make_config
    from dso_enroller.outbox import Outbox
    from dso_enroller.reader import SimulatedReader
    from dso_enroller.service import EnrolService
    cfg = make_config(tmp_path, tag_lock_enabled=True)
    reader, box = SimulatedReader(tag_delay=0.0), Outbox(cfg.outbox_path)
    queued_before_lock = []
    orig = reader.lock
    reader.lock = lambda pages: (queued_before_lock.append(box.stats()["pending"]), orig(pages))[1]
    res = EnrolService(cfg, reader, box).enrol(EnrolForm("Rigol", "DSO-0005", "R1", lock=True), lambda *a: None)
    assert res.locked and reader.locked and queued_before_lock == [1]
    with pytest.raises(ntag.WriteFailed):
        reader.write_ndef(b"\x03\x00\xfe\x00")                # a locked tag refuses rewrites
    box.close()


def test_lock_failure_leaves_a_registered_working_tag(tmp_path):
    from conftest import make_config
    from dso_enroller.outbox import Outbox
    from dso_enroller.reader import SimulatedReader
    from dso_enroller.service import EnrolService
    cfg = make_config(tmp_path, tag_lock_enabled=True)
    reader, box = SimulatedReader(tag_delay=0.0), Outbox(cfg.outbox_path)
    reader.lock = lambda pages: (_ for _ in ()).throw(ntag.NtagError("boom"))
    res = EnrolService(cfg, reader, box).enrol(EnrolForm("Rigol", "DSO-0006", "R1", lock=True), lambda *a: None)
    assert not res.locked and res.lock_error and box.stats()["pending"] == 1
    box.close()


# ---- images -----------------------------------------------------------------------------------
def test_image_prepare_shrinks_and_strips_metadata(tmp_path):
    p = tmp_path / "big.jpg"
    img = Image.new("RGB", (3000, 2000), (10, 120, 200))
    exif = Image.Exif(); exif[0x010F] = "CameraMaker"
    img.save(p, exif=exif)
    import base64, io
    out = Image.open(io.BytesIO(base64.b64decode(images.prepare_upload(str(p)))))
    assert max(out.size) <= 1024 and b"CameraMaker" not in base64.b64decode(images.prepare_upload(str(p)))


def test_image_rejects_non_images_and_wrong_extensions(tmp_path):
    (tmp_path / "x.jpg").write_bytes(b"not an image")
    (tmp_path / "x.gif").write_bytes(b"GIF89a")
    for name in ("x.jpg", "x.gif"):
        with pytest.raises(images.ImageError):
            images.prepare_upload(str(tmp_path / name))


# ---- outbox ------------------------------------------------------------------------------------
def test_outbox_survives_reopen_and_backs_off(tmp_path):
    box = Outbox(tmp_path / "o.db")
    rid = box.enqueue({"a": 1}, name="N N", room_no="R1")
    box.retry_later(rid, "network", attempts=3)
    assert box.due() == []                                    # waiting out its backoff
    box.close()
    again = Outbox(tmp_path / "o.db")
    assert again.stats()["pending"] == 1
    assert again.due(now=time.time() + 7200)[0]["id"] == rid
    again.mark_sent(rid, "asset-1")
    assert again.stats() == {"pending": 0, "failed": 0, "sent": 1}
    again.close()


# ---- PIN ------------------------------------------------------------------------------------------
def test_pin_policy_hashing_and_lockout(tmp_path):
    gate = PinGate(tmp_path)
    for bad in ("123", "111111", "123456", "abcdef", "1" * 20):
        with pytest.raises(PinPolicyError):
            gate.set_pin(bad)
    gate.set_pin("482915")
    assert "482915" not in (tmp_path / "pin.hash").read_text()
    assert gate.verify("482915") is True
    for _ in range(4):
        assert gate.verify("000111") is False
    with pytest.raises(PinLocked):
        gate.verify("000111")                                 # 5th failure locks
    with pytest.raises(PinLocked):
        PinGate(tmp_path).verify("482915")                    # a restart does not clear the lock


# ---- config ------------------------------------------------------------------------------------------
def write_cfg(home: Path, **over):
    home.mkdir(parents=True, exist_ok=True)
    (home / "device.key").write_text(os.urandom(32).hex()); (home / "tag.key").write_text(os.urandom(16).hex())
    values = {"backend_url": "https://assets.test", "public_host": "assets.test",
              "device_id": "11111111-1111-1111-1111-111111111111",
              "field_recipient_pub": X25519PrivateKey.generate().public_key().public_bytes_raw().hex(), **over}
    (home / "config.toml").write_text("\n".join(f'{k} = {v!r}'.replace("'", '"') if isinstance(v, str)
                                                else f"{k} = {str(v).lower()}" for k, v in values.items()))
    for f in home.iterdir():
        f.chmod(0o600)


def test_config_loads_and_enforces_https(tmp_path, monkeypatch):
    monkeypatch.setenv("DSO_ENROLLER_STATE", str(tmp_path / "state"))
    write_cfg(tmp_path / "h")
    assert cfgmod.load(tmp_path / "h").public_host == "assets.test"
    write_cfg(tmp_path / "h2", backend_url="http://assets.test")
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.load(tmp_path / "h2")
    write_cfg(tmp_path / "h3", backend_url="http://localhost:5000", allow_insecure_http=True)
    assert cfgmod.load(tmp_path / "h3").link_scheme == "http"
    write_cfg(tmp_path / "h4", backend_url="http://assets.test", allow_insecure_http=True)
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.load(tmp_path / "h4")                          # insecure http only ever for localhost


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_group_readable_key_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("DSO_ENROLLER_STATE", str(tmp_path / "state"))
    write_cfg(tmp_path / "h")
    (tmp_path / "h" / "device.key").chmod(0o640)
    with pytest.raises(cfgmod.ConfigError, match="chmod 600"):
        cfgmod.load(tmp_path / "h")
