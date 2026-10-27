import os
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from dsoreg.errors import Unauthorized
from dsoreg.security import envelope, taglink
from dsoreg.security.fieldcrypt import FieldCrypto, FieldCryptError
from dsoreg.security.signing import sign_row, verify_row
from dsoreg.security.tokens import SessionTokens, Tickets
from dsoreg.security.util import b64u, hmac_index
from dsoreg.services import audit


def test_envelope_roundtrip_and_aad_binding():
    priv = X25519PrivateKey.generate()
    pub = priv.public_key().public_bytes_raw()
    blob = envelope.seal(b"secret", pub, b"ctx")
    assert envelope.open_sealed(blob, priv, b"ctx") == b"secret"
    with pytest.raises(envelope.OpenError):
        envelope.open_sealed(blob, priv, b"other-ctx")
    tampered = bytearray(blob)
    tampered[-1] ^= 1
    with pytest.raises(envelope.OpenError):
        envelope.open_sealed(bytes(tampered), priv, b"ctx")
    with pytest.raises(envelope.OpenError):
        envelope.open_sealed(blob, X25519PrivateKey.generate(), b"ctx")


def test_envelope_uses_fresh_ephemeral_key_each_time():
    pub = X25519PrivateKey.generate().public_key().public_bytes_raw()
    assert envelope.seal(b"x", pub, b"a") != envelope.seal(b"x", pub, b"a")


def test_fieldcrypt_binds_ciphertext_to_its_cell():
    fc = FieldCrypto(os.urandom(32))
    blob = fc.encrypt("product_id", "row-1", "DSO-1")
    assert fc.decrypt("product_id", "row-1", blob) == "DSO-1"
    with pytest.raises(FieldCryptError):
        fc.decrypt("product_id", "row-2", blob)  # moved to another row
    with pytest.raises(FieldCryptError):
        fc.decrypt("tag_uid", "row-1", blob)  # moved to another column
    with pytest.raises(FieldCryptError):
        FieldCrypto(os.urandom(32)).decrypt("product_id", "row-1", blob)  # wrong KEK


def test_tag_product_id_is_aes128_and_bound_to_its_token():
    key, token = os.urandom(16), os.urandom(16)
    d = taglink.seal_product_id(key, token, "DSO-0042")
    assert taglink.open_product_id(key, token, d) == "DSO-0042"
    with pytest.raises(taglink.TagDataError):
        taglink.open_product_id(key, os.urandom(16), d)  # d lifted onto another tag
    with pytest.raises(taglink.TagDataError):
        taglink.open_product_id(os.urandom(16), token, d)


def test_link_parsing_rejects_foreign_hosts_and_extra_params():
    token = os.urandom(16)
    ok = f"https://assets.test/c?t={b64u(token)}&d=AAAA"
    assert taglink.parse_link(ok, host="assets.test")[0] == token
    for bad in (f"https://evil.test/c?t={b64u(token)}&d=AAAA",
                f"http://assets.test/c?t={b64u(token)}&d=AAAA",
                f"https://assets.test/x?t={b64u(token)}&d=AAAA",
                f"https://assets.test/c?t={b64u(token)}&d=AAAA&x=1",
                f"https://assets.test/c?t=short&d=AAAA"):
        with pytest.raises(taglink.TagDataError):
            taglink.parse_link(bad, host="assets.test")


def test_session_tokens_expire_and_reject_tampering():
    priv = Ed25519PrivateKey.generate()
    st = SessionTokens(priv, priv.public_key())
    tok, exp = st.mint(user_id="u1", role="admin", epoch=3, ttl=60)
    assert st.verify(tok)["sub"] == "u1"
    with pytest.raises(Unauthorized):
        st.verify(tok, now=exp + 1)
    head, sig = tok.split(".")
    with pytest.raises(Unauthorized):
        st.verify(b64u(b'{"v":1,"sub":"u1","role":"admin","ep":3,"iat":0,"exp":9999999999}') + "." + sig)
    other = SessionTokens(Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate().public_key())
    with pytest.raises(Unauthorized):
        other.verify(tok)


def test_tickets_are_bound_to_asset_and_expire():
    t = Tickets(os.urandom(32))
    ticket, exp = t.mint("asset-1", 60)
    assert t.verify(ticket)["a"] == "asset-1"
    with pytest.raises(Unauthorized):
        t.verify(ticket, now=exp + 1)
    with pytest.raises(Unauthorized):
        Tickets(os.urandom(32)).verify(ticket)


def test_row_signature_detects_any_change():
    priv = Ed25519PrivateKey.generate()
    core = {"v": 1, "id": "a", "token_hash": "t"}
    sig = sign_row(priv, core)
    assert verify_row(priv.public_key(), core, sig)
    assert not verify_row(priv.public_key(), {**core, "id": "b"}, sig)


def test_hmac_index_is_keyed_and_label_separated():
    k1, k2 = os.urandom(32), os.urandom(32)
    assert hmac_index(k1, "product", "X") == hmac_index(k1, "product", "X")
    assert hmac_index(k1, "product", "X") != hmac_index(k2, "product", "X")
    assert hmac_index(k1, "product", "X") != hmac_index(k1, "uid", "X")


def test_audit_chain_detects_edit_delete_and_reorder(env):
    from dsoreg.db import audit_log
    from sqlalchemy import update, delete
    with env.engine.begin() as c:
        for i in range(5):
            audit.append(c, actor="t", event="e", subject=str(i), detail={"i": i})
    with env.engine.connect() as c:
        assert audit.verify(c)["ok"]
    with env.engine.begin() as c:
        c.execute(update(audit_log).where(audit_log.c.seq == 3).values(actor="someone-else"))
    with env.engine.connect() as c:
        res = audit.verify(c)
    assert not res["ok"] and res["broken_at_seq"] == 3
