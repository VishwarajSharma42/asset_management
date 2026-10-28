"""Configuration and key loading.

Everything lives in one directory (default ~/.config/dso-enroller, override with
DSO_ENROLLER_HOME):  config.toml, device.key, tag.key, lock.key, pin.hash.

Key files must be readable by their owner only; the app refuses to start otherwise,
because a signing key that other accounts can read is not a signing key.
"""
from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .crypto import load_signing_key


class ConfigError(RuntimeError):
    pass


def home_dir() -> Path:
    override = os.environ.get("DSO_ENROLLER_HOME")
    return Path(override) if override else Path.home() / ".config" / "dso-enroller"


def state_dir() -> Path:
    d = Path(os.environ.get("DSO_ENROLLER_STATE") or (home_dir() / "state"))
    d.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        d.chmod(0o700)  # the outbox holds queued records; nobody else needs to read it
    return d


def _check_private(path: Path) -> None:
    if os.name == "posix":
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ConfigError(f"{path} is readable by other users. Run:  chmod 600 {path}")


def _read_key(base: Path, name: str, nbytes: int, *, required: bool = True) -> bytes | None:
    rel = name
    path = (base / rel) if not os.path.isabs(rel) else Path(rel)
    if not path.exists():
        if required:
            raise ConfigError(f"missing key file: {path}")
        return None
    _check_private(path)
    raw = bytes.fromhex(path.read_text(encoding="ascii").strip())
    if len(raw) != nbytes:
        raise ConfigError(f"{path} must hold {nbytes} bytes of hex")
    return raw


@dataclass(frozen=True)
class Config:
    home: Path
    backend_url: str
    public_host: str
    device_id: str
    field_recipient_pub: bytes
    device_key: Ed25519PrivateKey
    tag_key: bytes
    lock_key: bytes | None
    tag_lock_enabled: bool
    reader: str                      # "pn532" or "sim"
    pn532_interface: str             # "i2c" or "spi"
    allow_insecure_http: bool
    page_write_delay: float
    page_write_retries: int
    write_mode: str                  # "library" or "raw"
    ca_bundle: str | None
    outbox_path: Path
    log_path: Path
    allow_clone_tags: bool = False   # accept NTAG216-compatible tags whose vendor byte is not NXP

    @property
    def link_scheme(self) -> str:
        return "http" if self.allow_insecure_http else "https"


def load(home: Path | None = None) -> Config:
    home = home or home_dir()
    path = home / "config.toml"
    if not path.exists():
        raise ConfigError(f"no configuration at {path}. See docs/PI_SETUP.md.")
    _check_private(path)
    cfg = tomllib.loads(path.read_text(encoding="utf-8"))

    def need(key: str) -> str:
        value = str(cfg.get(key, "")).strip()
        if not value:
            raise ConfigError(f"config.toml is missing '{key}'")
        return value

    backend = need("backend_url").rstrip("/")
    parts = urlsplit(backend)
    insecure_ok = bool(cfg.get("allow_insecure_http", False))
    if parts.scheme != "https" and not (insecure_ok and parts.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ConfigError("backend_url must be https:// (plain http is only allowed for localhost with allow_insecure_http)")

    pub = bytes.fromhex(need("field_recipient_pub"))
    if len(pub) != 32:
        raise ConfigError("field_recipient_pub must be 32 bytes of hex")
    dev = _read_key(home, str(cfg.get("device_key_path", "device.key")), 32)
    tag = _read_key(home, str(cfg.get("tag_key_path", "tag.key")), 16)
    lock = _read_key(home, str(cfg.get("lock_key_path", "lock.key")), 32, required=False)

    reader = str(cfg.get("reader", "pn532"))
    if reader not in {"pn532", "sim"}:
        raise ConfigError("reader must be 'pn532' or 'sim'")
    return Config(
        home=home, backend_url=backend, public_host=need("public_host").lower(),
        device_id=need("device_id"), field_recipient_pub=pub,
        device_key=load_signing_key(dev.hex()), tag_key=tag, lock_key=lock,
        tag_lock_enabled=bool(cfg.get("tag_lock_enabled", False)), reader=reader,
        pn532_interface=str(cfg.get("pn532_interface", "i2c")), allow_insecure_http=insecure_ok,
        page_write_delay=float(cfg.get("page_write_delay", 0.05)),
        page_write_retries=int(cfg.get("page_write_retries", 3)),
        write_mode=str(cfg.get("write_mode", "library")), ca_bundle=cfg.get("ca_bundle"),
        allow_clone_tags=bool(cfg.get("allow_clone_tags", False)),
        outbox_path=state_dir() / "outbox.db", log_path=state_dir() / "enroller.log")
