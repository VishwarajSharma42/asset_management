#!/usr/bin/env python3
"""Generate every key the system needs. Run once, on a machine you trust.

    python scripts/gen_keys.py --out-dir secrets.local --host assets.college.edu \\
           --backend-url https://assets.college.edu

Writes (all in --out-dir, which is git-ignored):
    backend.env        everything the server needs EXCEPT the KEK
    KEK.hex            the key-encryption key. Keep it OFF the server's disk image and OUT of
                       backups: load it with systemd LoadCredential (docs/DEPLOY.md)
    pi/config.toml     the Pi app's settings
    pi/device.key      the Pi's Ed25519 private key (mode 600) — it never leaves the Pi
    pi/tag.key         16-byte key used to encrypt the product ID written on the tag
    pi/lock.key        master for per-tag lock passwords (only used when locking is enabled)
    DEVICE_REGISTER.txt  the exact command that registers the Pi on the server

--dev puts the KEK inline in backend.env and points everything at localhost, so you can
run the whole system on one laptop.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402

from dsoreg.security.keys import wrap  # noqa: E402

RAW_PRIV = dict(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption())
RAW_PUB = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="secrets.local")
    ap.add_argument("--host", default="localhost:5000", help="PUBLIC_HOST baked into every tag")
    ap.add_argument("--backend-url", default="http://localhost:5000")
    ap.add_argument("--database-url", default="")
    ap.add_argument("--dev", action="store_true")
    args = ap.parse_args()

    out = Path(args.out_dir)
    if out.exists() and any(out.iterdir()):
        print(f"refusing to overwrite {out}/ (not empty). Move it away first.", file=sys.stderr)
        return 1

    kek = secrets.token_bytes(32)
    fr = X25519PrivateKey.generate()
    row = Ed25519PrivateKey.generate()
    sess = Ed25519PrivateKey.generate()
    dev = Ed25519PrivateKey.generate()
    device_id = str(uuid.uuid4())

    env = {
        "DSOREG_ENV": "development" if args.dev else "production",
        "DATABASE_URL": args.database_url or (f"sqlite:///{(out.resolve() / 'dev.db').as_posix()}" if args.dev else "postgresql+psycopg:///dsoreg"),
        "PUBLIC_HOST": args.host,
        "FIELD_RECIPIENT_KEY_WRAPPED": wrap(kek, "field_recipient", fr.private_bytes(**RAW_PRIV)).hex(),
        "FIELD_RECIPIENT_PUB": fr.public_key().public_bytes(**RAW_PUB).hex(),
        "ROW_SIGNING_KEY_WRAPPED": wrap(kek, "row_signing", row.private_bytes(**RAW_PRIV)).hex(),
        "ROW_SIGNING_PUBKEY": row.public_key().public_bytes(**RAW_PUB).hex(),
        "SESSION_SIGNING_KEY_WRAPPED": wrap(kek, "session_signing", sess.private_bytes(**RAW_PRIV)).hex(),
        "SESSION_SIGNING_PUBKEY": sess.public_key().public_bytes(**RAW_PUB).hex(),
        "TICKET_KEY_HEX": secrets.token_hex(32),
        "TAG_INDEX_KEY_HEX": secrets.token_hex(32),
        "IP_HASH_SEED_HEX": secrets.token_hex(32),
    }
    tag_key = secrets.token_bytes(16)
    env["TAG_KEY_HEX"] = tag_key.hex()
    if args.dev:
        env["KEK_HEX"] = kek.hex()
        env["ALLOW_HTTP_LINKS"] = "1"
        env["MEDIA_DIR"] = (out.resolve() / "media").as_posix()
    else:
        env["TRUST_PROXY_HOPS"] = "1"
        env["MEDIA_DIR"] = "/var/lib/dsoreg/media"

    write_secret(out / "backend.env", "".join(f"{k}={v}\n" for k, v in env.items()))
    if not args.dev:
        write_secret(out / "KEK.hex", kek.hex() + "\n")

    write_secret(out / "pi" / "device.key", dev.private_bytes(**RAW_PRIV).hex() + "\n")
    write_secret(out / "pi" / "tag.key", tag_key.hex() + "\n")
    write_secret(out / "pi" / "lock.key", secrets.token_hex(32) + "\n")
    toml = (
        f'backend_url = "{args.backend_url}"\n'
        f'public_host = "{args.host}"\n'
        f'device_id = "{device_id}"\n'
        f'field_recipient_pub = "{env["FIELD_RECIPIENT_PUB"]}"\n'
        'device_key_path = "device.key"\n'
        'tag_key_path = "tag.key"\n'
        'lock_key_path = "lock.key"\n'
        'tag_lock_enabled = false\n'
        f'reader = "{"sim" if args.dev else "pn532"}"\n'
        f'allow_insecure_http = {"true" if args.dev else "false"}\n'
    )
    write_secret(out / "pi" / "config.toml", toml)
    pub = dev.public_key().public_bytes(**RAW_PUB).hex()
    write_secret(out / "DEVICE_REGISTER.txt",
                 "python -m dsoreg.cli --env-file backend.env register-device "
                 f"--device-id {device_id} --label pi-enroller-1 --public-key {pub}\n")

    print(f"wrote {out}/ ")
    print("Next: docs/DEPLOY.md (production) or docs/DEVELOPMENT.md (this laptop).")
    if not args.dev:
        print("IMPORTANT: move KEK.hex somewhere that is not the server's disk or its backups.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
