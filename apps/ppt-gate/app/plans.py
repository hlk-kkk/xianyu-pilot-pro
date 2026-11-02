from __future__ import annotations

from typing import Any

from .db import connect, utcnow_iso


DEFAULT_PLANS: tuple[dict[str, Any], ...] = (
    {
        "code": "trial",
        "name": "体验版",
        "valid_days": 7,
        "generations": 2,
        "ai_edits": 5,
        "image_generations": 0,
        "max_slides": 10,
        "price_cents": 0,
    },
    {
        "code": "basic",
        "name": "基础版",
        "valid_days": 30,
        "generations": 10,
        "ai_edits": 30,
        "image_generations": 0,
        "max_slides": 20,
        "price_cents": 990,
    },
    {
        "code": "pro",
        "name": "专业版",
        "valid_days": 30,
        "generations": 30,
        "ai_edits": 100,
        "image_generations": 20,
        "max_slides": 40,
        "price_cents": 2990,
    },
)


def seed_plans(database_path: str) -> None:
    now = utcnow_iso()
    conn = connect(database_path)
    try:
        for plan in DEFAULT_PLANS:
            conn.execute(
                "INSERT OR IGNORE INTO plans("
                "code, name, valid_days, generations, ai_edits, image_generations, "
                "max_slides, price_cents, active, created_at, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,1,?,?)",
                (
                    plan["code"],
                    plan["name"],
                    plan["valid_days"],
                    plan["generations"],
                    plan["ai_edits"],
                    plan["image_generations"],
                    plan["max_slides"],
                    plan["price_cents"],
                    now,
                    now,
                ),
            )
    finally:
        conn.close()


def get_plan(database_path: str, code: str) -> dict[str, Any] | None:
    conn = connect(database_path)
    try:
        row = conn.execute("SELECT * FROM plans WHERE code=? AND active=1", (code,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def get_plan_or_default(database_path: str, code: str) -> dict[str, Any]:
    plan = get_plan(database_path, code) or get_plan(database_path, "trial")
    if plan is None:
        raise RuntimeError("no active plan configured")
    return plan


def list_plans(database_path: str) -> list[dict[str, Any]]:
    conn = connect(database_path)
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM plans ORDER BY code").fetchall()]
    finally:
        conn.close()


def upsert_plan(database_path: str, plan: dict[str, Any]) -> dict[str, Any]:
    now = utcnow_iso()
    conn = connect(database_path)
    try:
        conn.execute(
            "INSERT INTO plans("
            "code, name, valid_days, generations, ai_edits, image_generations, "
            "max_slides, price_cents, active, created_at, updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, valid_days=excluded.valid_days, "
            "generations=excluded.generations, ai_edits=excluded.ai_edits, "
            "image_generations=excluded.image_generations, max_slides=excluded.max_slides, "
            "price_cents=excluded.price_cents, active=excluded.active, updated_at=excluded.updated_at",
            (
                plan["code"],
                plan["name"],
                int(plan["valid_days"]),
                int(plan["generations"]),
                int(plan["ai_edits"]),
                int(plan["image_generations"]),
                int(plan["max_slides"]),
                int(plan["price_cents"]),
                1 if plan.get("active", True) else 0,
                now,
                now,
            ),
        )
    finally:
        conn.close()
    result = get_plan(database_path, plan["code"])
    if result is None:
        raise RuntimeError("plan upsert failed")
    return result
