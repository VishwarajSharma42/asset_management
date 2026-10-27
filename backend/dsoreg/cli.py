"""Operator commands:  python -m dsoreg.cli <command>

    init-db          create tables (production schema is never auto-created)
    create-user      add a staff account (password is prompted, never a flag)
    register-device  register a Pi's public key
    revoke-device    stop accepting a device's signatures immediately
    verify-audit     walk the audit hash chain
    run-dev          run the development server from an env file
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import select, update


def load_env_file(path: str) -> None:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _ctx():
    from .app import create_app
    app = create_app()
    return app.extensions["dsoreg"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dsoreg", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", help="load KEY=VALUE lines before starting")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    cu = sub.add_parser("create-user")
    cu.add_argument("--username", required=True)
    cu.add_argument("--display-name", required=True)
    cu.add_argument("--role", choices=["admin", "viewer"], default="viewer")
    rd = sub.add_parser("register-device")
    rd.add_argument("--device-id", required=True)
    rd.add_argument("--label", required=True)
    rd.add_argument("--public-key", required=True, help="64 hex chars")
    rv = sub.add_parser("revoke-device")
    rv.add_argument("--device-id", required=True)
    sub.add_parser("verify-audit")
    rn = sub.add_parser("run-dev")
    rn.add_argument("--port", type=int, default=5000)
    args = ap.parse_args(argv)

    if args.env_file:
        load_env_file(args.env_file)

    from .db import devices, metadata
    from .services import audit as audit_svc
    from .services import users as users_svc
    from .db import utcnow

    if args.cmd == "run-dev":
        from .app import create_app
        create_app().run(host="127.0.0.1", port=args.port, debug=False)
        return 0

    from .app import create_app
    from .config import Settings
    from .db import make_engine
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)

    if args.cmd == "init-db":
        metadata.create_all(engine)
        print("tables created")
        return 0

    ctx = create_app(settings, engine).extensions["dsoreg"]
    if args.cmd == "create-user":
        pw = getpass.getpass("Password (min 12 chars): ")
        if pw != getpass.getpass("Again: "):
            print("passwords differ", file=sys.stderr)
            return 1
        out = users_svc.create_user(ctx, username=args.username, display_name=args.display_name,
                                    role=args.role, password=pw, actor="cli", now=utcnow())
        print(f"created {out['username']} ({out['role']})")
    elif args.cmd == "register-device":
        bytes.fromhex(args.public_key)
        if len(args.public_key) != 64:
            print("public key must be 64 hex chars", file=sys.stderr)
            return 1
        with engine.begin() as conn:
            conn.execute(devices.insert().values(device_id=args.device_id, label=args.label,
                                                 public_key=args.public_key.lower(), status="active",
                                                 created_at=utcnow()))
            audit_svc.append(conn, actor="cli", event="device.register", subject=args.device_id,
                             detail={"label": args.label})
        print("device registered")
    elif args.cmd == "revoke-device":
        with engine.begin() as conn:
            res = conn.execute(update(devices).where(devices.c.device_id == args.device_id)
                               .values(status="revoked"))
            audit_svc.append(conn, actor="cli", event="device.revoke", subject=args.device_id)
        print("revoked" if res.rowcount else "no such device")
    elif args.cmd == "verify-audit":
        with engine.connect() as conn:
            result = audit_svc.verify(conn)
        print(result)
        return 0 if result["ok"] else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
