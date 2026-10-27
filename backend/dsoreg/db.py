"""Tables and engine setup. SQLAlchemy Core, so the same code runs on PostgreSQL (production)
and SQLite (development and the test suite)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer, LargeBinary,
                        MetaData, String, Table, Text, UniqueConstraint, create_engine, event)
from sqlalchemy.types import TypeDecorator

metadata = MetaData()


class UTCDateTime(TypeDecorator):
    """Always timezone-aware UTC in Python, whatever the database hands back."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime refused; use timezone-aware UTC")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


users = Table(
    "users", metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("display_name", String(100), nullable=False),
    Column("role", String(16), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("token_epoch", Integer, nullable=False, default=0),
    Column("failed_count", Integer, nullable=False, default=0),
    Column("locked_until", UTCDateTime),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("last_login_at", UTCDateTime),
)

devices = Table(
    "devices", metadata,
    Column("device_id", String(36), primary_key=True),
    Column("label", String(100), nullable=False),
    Column("public_key", String(64), nullable=False),
    Column("status", String(16), nullable=False, default="active"),
    Column("created_at", UTCDateTime, nullable=False),
)

assets = Table(
    "assets", metadata,
    Column("id", String(36), primary_key=True),
    Column("product_index", String(64), nullable=False, unique=True),
    Column("product_id_ct", LargeBinary, nullable=False),
    Column("tag_uid_index", String(64), nullable=False, unique=True),
    Column("tag_uid_ct", LargeBinary, nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("link_ct", LargeBinary, nullable=False),
    Column("device_id", String(36), ForeignKey("devices.device_id"), nullable=False),
    Column("lock_requested", Boolean, nullable=False, default=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("row_sig", String(128), nullable=False),
    Column("row_key_version", Integer, nullable=False, default=1),
)
Index("ix_assets_device_created", assets.c.device_id, assets.c.created_at)

asset_profile = Table(
    "asset_profile", metadata,
    Column("asset_id", String(36), ForeignKey("assets.id"), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("room_no", String(20), nullable=False),
    Column("image_sha256", String(64)),
    Column("version", Integer, nullable=False, default=1),
    Column("updated_at", UTCDateTime, nullable=False),
    Column("updated_by", String(64)),
)
Index("ix_profile_room", asset_profile.c.room_no)

asset_state = Table(
    "asset_state", metadata,
    Column("asset_id", String(36), ForeignKey("assets.id"), primary_key=True),
    Column("lifecycle", String(16), nullable=False, default="active"),
    Column("condition", String(16), nullable=False, default="working"),
    Column("version", Integer, nullable=False, default=1),
    Column("updated_at", UTCDateTime, nullable=False),
)
Index("ix_state_condition", asset_state.c.condition)

usage_log = Table(
    "usage_log", metadata,
    Column("id", String(36), primary_key=True),
    Column("asset_id", String(36), ForeignKey("assets.id"), nullable=False),
    Column("user_name", String(100), nullable=False),
    Column("start_at", UTCDateTime, nullable=False),
    Column("end_at", UTCDateTime, nullable=False),
    Column("working", Boolean, nullable=False),
    Column("fault_note", String(500)),
    Column("submitted_at", UTCDateTime, nullable=False),
    Column("ip_hash", String(32), nullable=False),
    Column("ticket_jti", String(32), nullable=False, unique=True),
    # Where the person was when they submitted: AES-128-GCM of {"lat","lng","acc"}, bound to this row.
    # status: ok | denied (they refused) | unavailable (no GPS/insecure page/timeout) | none (not sent)
    Column("location_ct", LargeBinary),
    Column("location_status", String(12), nullable=False, default="none"),
)
Index("ix_usage_asset_start", usage_log.c.asset_id, usage_log.c.start_at)
Index("ix_usage_start", usage_log.c.start_at)

fault_report = Table(
    "fault_report", metadata,
    Column("id", String(36), primary_key=True),
    Column("asset_id", String(36), ForeignKey("assets.id"), nullable=False),
    Column("usage_id", String(36), ForeignKey("usage_log.id")),
    Column("note", String(500), nullable=False),
    Column("reported_by", String(100), nullable=False),
    Column("reported_at", UTCDateTime, nullable=False),
    Column("resolved_at", UTCDateTime),
    Column("resolved_by", String(64)),
    Column("resolution_note", String(500)),
)
Index("ix_fault_asset", fault_report.c.asset_id, fault_report.c.resolved_at)

idempotency = Table(
    "idempotency", metadata,
    Column("key", String(64), primary_key=True),
    Column("device_id", String(36), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("status", Integer, nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
)

audit_log = Table(
    "audit_log", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("ts", String(32), nullable=False),
    Column("actor", String(80), nullable=False),
    Column("event", String(48), nullable=False),
    Column("subject", String(80)),
    Column("detail", Text, nullable=False),
    Column("prev_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False, unique=True),
)

rate_limit = Table(
    "rate_limit", metadata,
    Column("key", String(120), primary_key=True),
    Column("window_start", Integer, nullable=False),
    Column("count", Integer, nullable=False),
)


def make_engine(url: str):
    kwargs = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # Documented pysqlite recipe: take the write lock at BEGIN, so two requests can
        # never read the same audit-chain head and both extend it.
        @event.listens_for(engine, "connect")
        def _connect(dbapi_conn, _):
            dbapi_conn.isolation_level = None
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            dbapi_conn.execute("PRAGMA journal_mode=WAL")

        @event.listens_for(engine, "begin")
        def _begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
    return engine
