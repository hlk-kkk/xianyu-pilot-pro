from __future__ import annotations

from .config import Settings
from .db import connect, utcnow_iso


DEFAULTS = {
    "daily_budget_cents": 10000,
    "estimated_generation_cents": 20,
    "estimated_ai_edit_cents": 5,
    "estimated_image_cents": 10,
    "max_concurrent_generations": 2,
    "max_concurrent_generations_per_card": 1,
    "metering_enabled": True,
    "deny_unknown_writes": False,
}


def seed_settings(database_path: str, settings: Settings) -> None:
    values = {
        "daily_budget_cents": settings.default_daily_budget_cents,
        "estimated_generation_cents": settings.estimated_generation_cents,
        "estimated_ai_edit_cents": settings.estimated_ai_edit_cents,
        "estimated_image_cents": settings.estimated_image_cents,
        "max_concurrent_generations": settings.max_concurrent_generations,
        "max_concurrent_generations_per_card": settings.max_concurrent_generations_per_card,
        "metering_enabled": settings.metering_enabled,
        "deny_unknown_writes": settings.deny_unknown_writes,
    }
    now = utcnow_iso()
    conn = connect(database_path)
    try:
        for key, value in values.items():
            conn.execute(
                "INSERT OR IGNORE INTO gate_settings(key, value, updated_at) VALUES(?,?,?)",
                (key, _encode(value), now),
            )
    finally:
        conn.close()


def _encode(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _decode(value: str, default: object) -> object:
    if isinstance(default, bool):
        return value in {"1", "true", "True", "yes", "on"}
    if isinstance(default, int):
        try:
            return int(value)
        except ValueError:
            return default
    return value


def get_setting(database_path: str, key: str) -> object:
    default = DEFAULTS[key]
    conn = connect(database_path)
    try:
        row = conn.execute("SELECT value FROM gate_settings WHERE key=?", (key,)).fetchone()
        return _decode(row["value"], default) if row is not None else default
    finally:
        conn.close()


def get_setting_from_conn(conn, key: str) -> object:
    default = DEFAULTS[key]
    row = conn.execute("SELECT value FROM gate_settings WHERE key=?", (key,)).fetchone()
    return _decode(row["value"], default) if row is not None else default


def set_setting(database_path: str, key: str, value: object) -> None:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting: {key}")
    conn = connect(database_path)
    try:
        conn.execute(
            "INSERT INTO gate_settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, _encode(value), utcnow_iso()),
        )
    finally:
        conn.close()


def all_settings(database_path: str) -> dict[str, object]:
    return {key: get_setting(database_path, key) for key in DEFAULTS}
