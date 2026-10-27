#!/usr/bin/env python3
"""Regenerate vectors/crypto.json from the BACKEND implementation.

The backend and the Pi each carry their own copy of the sealing, tag-encryption and
signing code (they ship to different machines and never import each other). This file is
the contract between them: both test suites load it and must reproduce every value.
Run this only when the wire format deliberately changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402

from dsoreg.security import envelope, taglink  # noqa: E402
from dsoreg.security.signing import canonical_request  # noqa: E402

RAW_PRIV = dict(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption())
RAW_PUB = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def fixed(n: int, seed: int) -> bytes:
    return bytes((seed + i * 7) % 256 for i in range(n))


def main() -> None:
    recip = X25519PrivateKey.from_private_bytes(fixed(32, 1))
    eph = X25519PrivateKey.from_private_bytes(fixed(32, 2))
    recip_pub = recip.public_key().public_bytes(**RAW_PUB)
    nonce = fixed(12, 3)
    aad = b"enrol|11111111-1111-1111-1111-111111111111|" + b"ab" * 32
    plaintext = b'{"link":"https://assets.test/c?t=x&d=y","product_id":"DSO-0001","tag_uid":"04A1B2C3D4E5F6"}'
    sealed = envelope.seal(plaintext, recip_pub, aad, _eph=eph, _nonce=nonce)

    tag_key, token, tag_nonce = fixed(16, 4), fixed(16, 5), fixed(12, 6)
    d = taglink.seal_product_id(tag_key, token, "DSO-0001", _nonce=tag_nonce)

    dev = Ed25519PrivateKey.from_private_bytes(fixed(32, 7))
    body = b'{"schema":"dsoreg.enrol.v1","name":"Scope"}'
    message = canonical_request("POST", "/api/v1/enrol", "1790000000", "idem-0123456789abcdef", body)

    vectors = {
        "envelope": {"recipient_priv": recip.private_bytes(**RAW_PRIV).hex(), "recipient_pub": recip_pub.hex(),
                     "ephemeral_priv": eph.private_bytes(**RAW_PRIV).hex(), "nonce": nonce.hex(),
                     "aad": aad.hex(), "plaintext": plaintext.hex(), "sealed": sealed.hex()},
        "taglink": {"tag_key": tag_key.hex(), "token": token.hex(), "nonce": tag_nonce.hex(),
                    "product_id": "DSO-0001", "d": d},
        "signing": {"device_priv": dev.private_bytes(**RAW_PRIV).hex(), "method": "POST",
                    "path": "/api/v1/enrol", "timestamp": "1790000000", "idem": "idem-0123456789abcdef",
                    "body": body.hex(), "message": message.hex(), "signature": dev.sign(message).hex()},
    }
    out = ROOT / "vectors" / "crypto.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(vectors, indent=2) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
