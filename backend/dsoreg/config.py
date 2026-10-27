"""Settings, loaded once from the environment.

Production refuses to start with anything missing. There is no default for a secret
anywhere in this file: a default secret is a published secret.
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def _hex_env(env: dict, name: str, length: int | None = None, *, required: bool = True) -> bytes:
    raw = (env.get(name) or "").strip()
    if not raw:
        if required:
            raise ConfigError(f"{name} is required")
        return b""
    try:
        value = bytes.fromhex(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be hex") from exc
    if length is not None and len(value) != length:
        raise ConfigError(f"{name} must be {length} bytes ({length * 2} hex chars)")
    return value


@dataclass(frozen=True)
class Settings:
    env: str
    database_url: str
    kek: bytes
    field_recipient_key_wrapped: bytes
    field_recipient_pub: bytes
    row_signing_key_wrapped: bytes
    row_signing_pubkey: bytes
    session_signing_key_wrapped: bytes
    session_signing_pubkey: bytes
    ticket_key: bytes
    tag_index_key: bytes
    tag_key: bytes
    ip_hash_seed: bytes
    public_host: str
    media_dir: str
    frontend_dir: str
    allow_http_links: bool = False
    trust_proxy_hops: int = 0
    scan_allowed_cidrs: tuple = ()
    ticket_ttl_s: int = 1800
    session_ttl_s: int = 1800
    timestamp_window_s: int = 90
    device_daily_enrol_limit: int = 300
    max_body_bytes: int = 3 * 1024 * 1024
    hsts: bool = True
    serve_frontend: bool = True
    location_required: bool = False

    @property
    def production(self) -> bool:
        return self.env == "production"

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        mode = env.get("DSOREG_ENV", "production").strip().lower()
        if mode not in {"production", "development", "testing"}:
            raise ConfigError("DSOREG_ENV must be production, development or testing")

        kek_file = (env.get("KEK_FILE") or "").strip()
        if kek_file:
            env["KEK_HEX"] = Path(kek_file).read_text(encoding="ascii").strip()

        db = (env.get("DATABASE_URL") or "").strip()
        if not db:
            if mode == "production":
                raise ConfigError("DATABASE_URL is required")
            db = "sqlite:///dsoreg-dev.db"

        public_host = (env.get("PUBLIC_HOST") or "").strip().lower()
        if not public_host or "/" in public_host or (":" in public_host and mode == "production"):
            raise ConfigError("PUBLIC_HOST must be a bare hostname")

        cidrs = []
        for part in (env.get("SCAN_ALLOWED_CIDRS") or "").split(","):
            part = part.strip()
            if part:
                try:
                    cidrs.append(ipaddress.ip_network(part, strict=False))
                except ValueError as exc:
                    raise ConfigError(f"SCAN_ALLOWED_CIDRS has a bad entry: {part}") from exc

        base = Path(__file__).resolve().parents[2]
        return cls(
            env=mode,
            database_url=db,
            kek=_hex_env(env, "KEK_HEX", 32),
            field_recipient_key_wrapped=_hex_env(env, "FIELD_RECIPIENT_KEY_WRAPPED"),
            field_recipient_pub=_hex_env(env, "FIELD_RECIPIENT_PUB", 32),
            row_signing_key_wrapped=_hex_env(env, "ROW_SIGNING_KEY_WRAPPED"),
            row_signing_pubkey=_hex_env(env, "ROW_SIGNING_PUBKEY", 32),
            session_signing_key_wrapped=_hex_env(env, "SESSION_SIGNING_KEY_WRAPPED"),
            session_signing_pubkey=_hex_env(env, "SESSION_SIGNING_PUBKEY", 32),
            ticket_key=_hex_env(env, "TICKET_KEY_HEX", 32),
            tag_index_key=_hex_env(env, "TAG_INDEX_KEY_HEX", 32),
            tag_key=_hex_env(env, "TAG_KEY_HEX", 16),
            ip_hash_seed=_hex_env(env, "IP_HASH_SEED_HEX", 32),
            public_host=public_host,
            media_dir=env.get("MEDIA_DIR") or str(base / "media"),
            frontend_dir=env.get("FRONTEND_DIR") or str(base / "frontend"),
            allow_http_links=mode != "production" and env.get("ALLOW_HTTP_LINKS", "0") == "1",
            trust_proxy_hops=int(env.get("TRUST_PROXY_HOPS", "0")),
            scan_allowed_cidrs=tuple(cidrs),
            ticket_ttl_s=int(env.get("TICKET_TTL_S", "1800")),
            session_ttl_s=int(env.get("SESSION_TTL_S", "1800")),
            device_daily_enrol_limit=int(env.get("DEVICE_DAILY_ENROL_LIMIT", "300")),
            hsts=env.get("HSTS", "1") == "1",
            serve_frontend=env.get("SERVE_FRONTEND", "1" if mode != "production" else "0") == "1",
            location_required=env.get("LOCATION_REQUIRED", "0") == "1",
        )
