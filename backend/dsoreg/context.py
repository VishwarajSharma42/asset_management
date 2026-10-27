"""Everything a request needs, built once at start-up."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from .config import Settings
from .security.fieldcrypt import FieldCrypto
from .security.keys import KeyRing
from .security.tokens import SessionTokens, Tickets


@dataclass(frozen=True)
class Ctx:
    settings: Settings
    engine: Engine
    keys: KeyRing
    fields: FieldCrypto
    sessions: SessionTokens
    tickets: Tickets

    @classmethod
    def build(cls, settings: Settings, engine: Engine) -> "Ctx":
        keys = KeyRing.load(settings)
        return cls(settings, engine, keys, FieldCrypto(settings.kek),
                   SessionTokens(keys.session_signing_priv, keys.session_signing_pub),
                   Tickets(settings.ticket_key))
