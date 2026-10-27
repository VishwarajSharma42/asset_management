"""The backend half of the cross-implementation contract (the Pi runs the same file)."""
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from dsoreg.security import envelope, taglink
from dsoreg.security.signing import canonical_request

V = json.loads((Path(__file__).resolve().parents[2] / "vectors" / "crypto.json").read_text())


def test_envelope_vector_opens_and_reproduces():
    e = V["envelope"]
    recip = X25519PrivateKey.from_private_bytes(bytes.fromhex(e["recipient_priv"]))
    blob = bytes.fromhex(e["sealed"])
    assert envelope.open_sealed(blob, recip, bytes.fromhex(e["aad"])) == bytes.fromhex(e["plaintext"])
    again = envelope.seal(bytes.fromhex(e["plaintext"]), bytes.fromhex(e["recipient_pub"]),
                          bytes.fromhex(e["aad"]),
                          _eph=X25519PrivateKey.from_private_bytes(bytes.fromhex(e["ephemeral_priv"])),
                          _nonce=bytes.fromhex(e["nonce"]))
    assert again == blob


def test_taglink_vector():
    t = V["taglink"]
    d = taglink.seal_product_id(bytes.fromhex(t["tag_key"]), bytes.fromhex(t["token"]), t["product_id"],
                                _nonce=bytes.fromhex(t["nonce"]))
    assert d == t["d"]
    assert taglink.open_product_id(bytes.fromhex(t["tag_key"]), bytes.fromhex(t["token"]), d) == t["product_id"]


def test_signing_vector():
    s = V["signing"]
    msg = canonical_request(s["method"], s["path"], s["timestamp"], s["idem"], bytes.fromhex(s["body"]))
    assert msg.hex() == s["message"]
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(s["device_priv"]))
    assert key.sign(msg).hex() == s["signature"]
