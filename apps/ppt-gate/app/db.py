from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS card_batches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan TEXT NOT NULL,
    valid_days INTEGER NOT NULL,
    total INTEGER NOT NULL,
    xianyu_group_id INTEGER,
    xianyu_import_status TEXT,
    xianyu_imported_count INTEGER NOT NULL DEFAULT 0,
    xianyu_import_error TEXT,
    xianyu_imported_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES card_batches(id),
    code_hash TEXT NOT NULL UNIQUE,
    code_prefix TEXT NOT NULL,
    plan TEXT NOT NULL,
    valid_days INTEGER NOT NULL DEFAULT 7,
    status TEXT NOT NULL DEFAULT 'available',
    presenton_username TEXT,
    presenton_user_id TEXT,
    credential_enc TEXT,
    expires_at TEXT,
    redeemed_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_batch ON cards(batch_id);

CREATE TABLE IF NOT EXISTS presenton_users (
    username TEXT PRIMARY KEY,
    presenton_user_id TEXT NOT NULL UNIQUE,
    card_id TEXT NOT NULL UNIQUE REFERENCES cards(id),
    credential_enc TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    valid_days INTEGER NOT NULL,
    generations INTEGER NOT NULL,
    ai_edits INTEGER NOT NULL,
    image_generations INTEGER NOT NULL,
    max_slides INTEGER NOT NULL,
    price_cents INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS card_quotas (
    card_id TEXT PRIMARY KEY REFERENCES cards(id),
    generations_total INTEGER NOT NULL,
    generations_used INTEGER NOT NULL DEFAULT 0,
    generations_reserved INTEGER NOT NULL DEFAULT 0,
    ai_edits_total INTEGER NOT NULL,
    ai_edits_used INTEGER NOT NULL DEFAULT 0,
    ai_edits_reserved INTEGER NOT NULL DEFAULT 0,
    image_generations_total INTEGER NOT NULL,
    image_generations_used INTEGER NOT NULL DEFAULT 0,
    image_generations_reserved INTEGER NOT NULL DEFAULT 0,
    max_slides INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_reservations (
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(id),
    action TEXT NOT NULL,
    quota_kind TEXT NOT NULL DEFAULT 'generation',
    units INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'reserved',
    reference TEXT,
    request_key TEXT,
    estimated_cost_cents INTEGER NOT NULL DEFAULT 0,
    settled_cost_cents INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    last_checked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reservations_status ON usage_reservations(status);
CREATE INDEX IF NOT EXISTS idx_reservations_card ON usage_reservations(card_id);
CREATE INDEX IF NOT EXISTS idx_reservations_reference ON usage_reservations(reference);

CREATE TABLE IF NOT EXISTS gate_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    card_id TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def connect(database_path: str) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    if name not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _migrate(conn: sqlite3.Connection) -> None:
    for name, ddl in (
        ("xianyu_group_id", "INTEGER"),
        ("xianyu_import_status", "TEXT"),
        ("xianyu_imported_count", "INTEGER NOT NULL DEFAULT 0"),
        ("xianyu_import_error", "TEXT"),
        ("xianyu_imported_at", "TEXT"),
    ):
        _ensure_column(conn, "card_batches", name, ddl)

    for name, ddl in (
        ("quota_kind", "TEXT NOT NULL DEFAULT 'generation'"),
        ("request_key", "TEXT"),
        ("estimated_cost_cents", "INTEGER NOT NULL DEFAULT 0"),
        ("settled_cost_cents", "INTEGER NOT NULL DEFAULT 0"),
        ("expires_at", "TEXT"),
        ("last_checked_at", "TEXT"),
    ):
        _ensure_column(conn, "usage_reservations", name, ddl)


def init_db(database_path: str) -> None:
    conn = connect(database_path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
    finally:
        conn.close()
