import os
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select, text, update

from dsoreg.db import assets, devices

from conftest import Env, png_b64


def test_enrol_happy_path_stores_no_plaintext_product_id(env):
    out = env.enrol_ok(product_id="DSO-SECRET-77", name="Rigol DS1054Z", room="EXTC-3")
    assert out["status"] == "enrolled" and out["asset_id"]
    with env.engine.connect() as c:
        row = c.execute(select(assets)).one()
        dump = "".join(str(v) for v in row) + repr(row.product_id_ct) + repr(row.link_ct)
        # A raw dump of the database holds no readable product ID, UID or link.
        assert "DSO-SECRET-77" not in dump and "04A1B2C3D4E5F6" not in dump and "assets.test" not in dump
        assert b"DSO-SECRET-77" not in bytes(row.product_id_ct)


def test_replay_returns_stored_response_without_a_second_row(env):
    body = env.pi.payload()
    headers, raw = env.pi.request(body, idem="idem-replay-0001-abcd")
    first = env.client.post("/api/v1/enrol", data=raw, headers=headers)
    again = env.client.post("/api/v1/enrol", data=raw, headers=headers)
    assert first.status_code == again.status_code == 201
    assert first.get_json() == again.get_json()
    with env.engine.connect() as c:
        assert len(c.execute(select(assets)).all()) == 1


def test_idempotency_key_cannot_be_reused_for_a_different_body(env):
    h1, r1 = env.pi.request(env.pi.payload(product_id="A-1"), idem="idem-reuse-000001xx")
    assert env.client.post("/api/v1/enrol", data=r1, headers=h1).status_code == 201
    h2, r2 = env.pi.request(env.pi.payload(product_id="A-2"), idem="idem-reuse-000001xx")
    r = env.client.post("/api/v1/enrol", data=r2, headers=h2)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "idempotency_key_reuse"


def test_bad_signature_unknown_device_stale_clock_all_rejected(env):
    body = env.pi.payload()
    # signed by a key the server does not know
    h, raw = env.pi.request(body, sign_with=Ed25519PrivateKey.generate())
    assert env.client.post("/api/v1/enrol", data=raw, headers=h).status_code == 401
    # unknown device id gives the same answer
    h, raw = env.pi.request(body, device_id="11111111-1111-1111-1111-111111111111")
    assert env.client.post("/api/v1/enrol", data=raw, headers=h).get_json()["error"]["code"] == "bad_signature"
    # body modified after signing
    h, raw = env.pi.request(body)
    assert env.client.post("/api/v1/enrol", data=raw + b" ", headers=h).status_code == 401
    # captured long ago and replayed
    h, raw = env.pi.request(body, ts=int(time.time()) - 600)
    r = env.client.post("/api/v1/enrol", data=raw, headers=h)
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "stale_timestamp"
    with env.engine.connect() as c:
        assert c.execute(select(assets)).all() == []


def test_signature_is_bound_to_the_path(env):
    from dsoreg.security.signing import canonical_request
    body = env.pi.payload()
    raw = __import__("json").dumps(body).encode()
    ts, idem = str(int(time.time())), "idem-path-binding-001"
    sig = env.pi.key.sign(canonical_request("POST", "/api/v1/other", ts, idem, raw)).hex()
    h = {"X-Device-Id": env.device_id, "X-Timestamp": ts, "X-Idempotency-Key": idem,
         "X-Signature": sig, "Content-Type": "application/json"}
    assert env.client.post("/api/v1/enrol", data=raw, headers=h).status_code == 401


def test_revoked_device_is_refused_even_with_a_valid_signature(env):
    with env.engine.begin() as c:
        c.execute(update(devices).values(status="revoked"))
    r = env.enrol()
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "device_revoked"


def test_duplicate_product_or_tag_is_one_generic_conflict(env):
    env.enrol_ok(product_id="DSO-1", tag_uid="04AAAAAAAAAAAA")
    r1 = env.enrol(product_id="DSO-1", tag_uid="04BBBBBBBBBBBB")
    r2 = env.enrol(product_id="DSO-2", tag_uid="04AAAAAAAAAAAA")
    assert r1.status_code == r2.status_code == 409
    assert r1.get_json()["error"]["message"] == r2.get_json()["error"]["message"]


def test_link_must_point_at_our_host_and_match_the_product(env):
    token, foreign = env.pi.link("DSO-9", host="evil.example")
    r = env.enrol(product_id="DSO-9", link=foreign, token=token)
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "bad_link"
    # d sealed for a different product than the record claims
    token, link = env.pi.link("DSO-OTHER")
    r = env.enrol(product_id="DSO-9", link=link, token=token)
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "bad_link"


def test_token_hash_must_match_the_link(env):
    token, link = env.pi.link("DSO-5")
    r = env.enrol(product_id="DSO-5", link=link, token=os.urandom(16))
    assert r.status_code == 422


def test_sealed_record_bound_to_device_and_token(env):
    body = env.pi.payload()
    body["token_hash"] = "0" * 64  # sealed AAD includes the token hash
    h, raw = env.pi.request(body)
    r = env.client.post("/api/v1/enrol", data=raw, headers=h)
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "bad_seal"


def test_unknown_fields_and_bad_values_are_rejected(env):
    assert env.enrol(override={"is_admin": True}).status_code == 400
    assert env.enrol(room="<script>").status_code == 422
    assert env.enrol(name="x").status_code == 422
    assert env.enrol(product_id="bad id!").status_code == 422


def test_daily_device_quota_is_enforced(tmp_path):
    e = Env(tmp_path, device_daily_enrol_limit=2)
    e.enrol_ok(product_id="Q-1", tag_uid="04000000000001")
    e.enrol_ok(product_id="Q-2", tag_uid="04000000000002")
    r = e.enrol(product_id="Q-3", tag_uid="04000000000003")
    assert r.status_code == 429 and r.get_json()["error"]["code"] == "device_quota_exceeded"


def test_image_is_reencoded_and_metadata_stripped(env):
    out = env.enrol_ok(image=png_b64(exif=True))
    with env.engine.connect() as c:
        pass
    files = list((__import__("pathlib").Path(env.settings.media_dir)).glob("*.jpg"))
    assert len(files) == 1
    data = files[0].read_bytes()
    assert data[:3] == b"\xff\xd8\xff" and b"SecretCameraMaker" not in data
    assert files[0].stem == __import__("hashlib").sha256(data).hexdigest()


def test_non_images_and_oversize_uploads_are_refused(env):
    import base64
    assert env.enrol(image=base64.b64encode(b"<?php system($_GET[0]); ?>").decode()).status_code == 422
    assert env.enrol(image=base64.b64encode(b"GIF89a" + b"\0" * 100).decode()).status_code == 422
    assert env.enrol(image="not base64!!").status_code == 422
    big = base64.b64encode(os.urandom(2_200_000)).decode()
    assert env.enrol(image=big).status_code in (413, 422)


def test_audit_entry_written_in_same_transaction(env):
    env.enrol_ok()
    with env.engine.connect() as c:
        rows = c.execute(text("select event, actor from audit_log")).all()
    assert [r[0] for r in rows] == ["asset.enrol"] and rows[0][1].startswith("device:")
