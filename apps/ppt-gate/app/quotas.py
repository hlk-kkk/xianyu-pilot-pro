from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from .config import Settings
from .db import connect, parse_iso, utcnow, utcnow_iso
from .gate_settings import get_setting_from_conn


KIND_COLUMNS = {
    "generation": ("generations_total", "generations_used", "generations_reserved"),
    "ai_edit": ("ai_edits_total", "ai_edits_used", "ai_edits_reserved"),
    "image": (
        "image_generations_total",
        "image_generations_used",
        "image_generations_reserved",
    ),
}


class QuotaExceeded(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class ConcurrencyExceeded(Exception):
    pass


def _row_dict(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _ensure_quota_conn(conn, card: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM card_quotas WHERE card_id=?", (card["id"],)
    ).fetchone()
    if row is not None:
        return dict(row)
    plan = conn.execute(
        "SELECT * FROM plans WHERE code=? AND active=1", (card["plan"],)
    ).fetchone()
    if plan is None:
        plan = conn.execute(
            "SELECT * FROM plans WHERE code='trial' AND active=1"
        ).fetchone()
    if plan is None:
        raise QuotaExceeded("no active plan configured")
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO card_quotas("
        "card_id, generations_total, generations_used, generations_reserved, "
        "ai_edits_total, ai_edits_used, ai_edits_reserved, "
        "image_generations_total, image_generations_used, image_generations_reserved, "
        "max_slides, updated_at"
        ") VALUES(?,?,0,0,?,0,0,?,0,0,?,?)",
        (
            card["id"],
            plan["generations"],
            plan["ai_edits"],
            plan["image_generations"],
            plan["max_slides"],
            now,
        ),
    )
    return dict(
        conn.execute("SELECT * FROM card_quotas WHERE card_id=?", (card["id"],)).fetchone()
    )


def ensure_quota(database_path: str, card: dict[str, Any]) -> dict[str, Any]:
    conn = connect(database_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        quota = _ensure_quota_conn(conn, card)
        conn.execute("COMMIT")
        return quota
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_quota(database_path: str, card_id: str) -> dict[str, Any] | None:
    conn = connect(database_path)
    try:
        return _row_dict(
            conn.execute("SELECT * FROM card_quotas WHERE card_id=?", (card_id,)).fetchone()
        )
    finally:
        conn.close()


def estimate_cost(database_path: str, kind: str) -> int:
    key = {
        "generation": "estimated_generation_cents",
        "ai_edit": "estimated_ai_edit_cents",
        "image": "estimated_image_cents",
    }[kind]
    conn = connect(database_path)
    try:
        return int(get_setting_from_conn(conn, key))
    finally:
        conn.close()


def reserve(
    database_path: str,
    *,
    card_id: str,
    kind: str,
    action: str,
    units: int,
    estimated_cost_cents: int,
    request_key: str | None,
    settings: Settings,
) -> tuple[dict[str, Any], bool]:
    """Reserve quota and budget before forwarding a metered request.

    Returns ``(reservation, duplicate)``. A duplicate request key returns the
    existing reservation instead of creating a second charge.
    """
    if kind not in KIND_COLUMNS:
        raise ValueError(f"unknown quota kind: {kind}")
    units = max(1, int(units))
    conn = connect(database_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        card_row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        if card_row is None or card_row["status"] != "active":
            raise QuotaExceeded("兑换码不可用")
        card = dict(card_row)
        expires_at = parse_iso(card.get("expires_at"))
        if expires_at is not None and expires_at < utcnow():
            raise QuotaExceeded("兑换码已过期")

        if request_key:
            existing = conn.execute(
                "SELECT * FROM usage_reservations WHERE card_id=? AND request_key=?",
                (card_id, request_key),
            ).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                return dict(existing), True

        quota = _ensure_quota_conn(conn, card)
        total_col, used_col, reserved_col = KIND_COLUMNS[kind]
        total = int(quota[total_col])
        used = int(quota[used_col])
        reserved = int(quota[reserved_col])
        if total >= 0 and used + reserved + units > total:
            raise QuotaExceeded(
                "本次操作需要 {} 次额度，当前已用 {}、处理中 {}、上限 {}".format(
                    units, used, reserved, total
                )
            )

        if kind == "generation":
            global_limit = int(get_setting_from_conn(conn, "max_concurrent_generations"))
            if global_limit >= 0:
                active = conn.execute(
                    "SELECT COUNT(*) FROM usage_reservations "
                    "WHERE status='reserved' AND quota_kind='generation'"
                ).fetchone()[0]
                if active >= global_limit:
                    raise ConcurrencyExceeded("当前生成任务较多，请稍后再试")
            card_limit = int(
                get_setting_from_conn(conn, "max_concurrent_generations_per_card")
            )
            if card_limit >= 0:
                active_for_card = conn.execute(
                    "SELECT COUNT(*) FROM usage_reservations "
                    "WHERE status='reserved' AND quota_kind='generation' AND card_id=?",
                    (card_id,),
                ).fetchone()[0]
                if active_for_card >= card_limit:
                    raise ConcurrencyExceeded("该兑换码已有生成任务在处理中")

        daily_budget = int(get_setting_from_conn(conn, "daily_budget_cents"))
        if daily_budget >= 0:
            today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            settled = conn.execute(
                "SELECT COALESCE(SUM(settled_cost_cents),0) FROM usage_reservations "
                "WHERE status='settled' AND created_at>=?",
                (today,),
            ).fetchone()[0]
            pending = conn.execute(
                "SELECT COALESCE(SUM(estimated_cost_cents),0) FROM usage_reservations "
                "WHERE status='reserved' AND created_at>=?",
                (today,),
            ).fetchone()[0]
            if int(settled) + int(pending) + estimated_cost_cents > daily_budget:
                raise BudgetExceeded("今日预算已用尽，请稍后再试或联系运营")

        now = utcnow()
        reservation_id = "res_" + uuid.uuid4().hex
        conn.execute(
            "INSERT INTO usage_reservations("
            "id, card_id, action, quota_kind, units, status, reference, request_key, "
            "estimated_cost_cents, settled_cost_cents, expires_at, last_checked_at, "
            "created_at, updated_at"
            ") VALUES(?,?,?,?,?,'reserved',NULL,?,?,0,?,?,?,?)",
            (
                reservation_id,
                card_id,
                action,
                kind,
                units,
                request_key,
                estimated_cost_cents,
                (now + timedelta(seconds=settings.reservation_ttl_seconds)).isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        conn.execute(
            f"UPDATE card_quotas SET {reserved_col}={reserved_col}+?, updated_at=? WHERE card_id=?",
            (units, now.isoformat(), card_id),
        )
        row = conn.execute(
            "SELECT * FROM usage_reservations WHERE id=?", (reservation_id,)
        ).fetchone()
        conn.execute("COMMIT")
        return dict(row), False
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def attach_reference(database_path: str, reservation_id: str, reference: str) -> None:
    conn = connect(database_path)
    try:
        conn.execute(
            "UPDATE usage_reservations SET reference=?, updated_at=? WHERE id=?",
            (reference, utcnow_iso(), reservation_id),
        )
    finally:
        conn.close()


def touch(database_path: str, reservation_id: str) -> None:
    conn = connect(database_path)
    try:
        conn.execute(
            "UPDATE usage_reservations SET last_checked_at=?, updated_at=? WHERE id=?",
            (utcnow_iso(), utcnow_iso(), reservation_id),
        )
    finally:
        conn.close()


def settle(
    database_path: str,
    reservation_id: str,
    *,
    cost_cents: int | None = None,
    reference: str | None = None,
) -> dict[str, Any] | None:
    conn = connect(database_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM usage_reservations WHERE id=?", (reservation_id,)
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        if row["status"] != "reserved":
            conn.execute("COMMIT")
            return dict(row)
        cost = int(row["estimated_cost_cents"] if cost_cents is None else cost_cents)
        _, used_col, reserved_col = KIND_COLUMNS[row["quota_kind"]]
        conn.execute(
            "UPDATE usage_reservations SET status='settled', settled_cost_cents=?, "
            "reference=COALESCE(?, reference), updated_at=? WHERE id=?",
            (cost, reference, utcnow_iso(), reservation_id),
        )
        conn.execute(
            f"UPDATE card_quotas SET {reserved_col}={reserved_col}-?, "
            f"{used_col}={used_col}+?, updated_at=? WHERE card_id=?",
            (row["units"], row["units"], utcnow_iso(), row["card_id"]),
        )
        updated = conn.execute(
            "SELECT * FROM usage_reservations WHERE id=?", (reservation_id,)
        ).fetchone()
        conn.execute("COMMIT")
        return dict(updated)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def release(database_path: str, reservation_id: str, reason: str = "") -> dict[str, Any] | None:
    conn = connect(database_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM usage_reservations WHERE id=?", (reservation_id,)
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        if row["status"] not in {"reserved", "unknown"}:
            conn.execute("COMMIT")
            return dict(row)
        _, _, reserved_col = KIND_COLUMNS[row["quota_kind"]]
        conn.execute(
            "UPDATE usage_reservations SET status='released', "
            "reference=COALESCE(reference, ?), updated_at=? WHERE id=?",
            (reason[:200] if reason else None, utcnow_iso(), reservation_id),
        )
        conn.execute(
            f"UPDATE card_quotas SET {reserved_col}=MAX(0,{reserved_col}-?), updated_at=? WHERE card_id=?",
            (row["units"], utcnow_iso(), row["card_id"]),
        )
        updated = conn.execute(
            "SELECT * FROM usage_reservations WHERE id=?", (reservation_id,)
        ).fetchone()
        conn.execute("COMMIT")
        return dict(updated)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def mark_unknown(database_path: str, reservation_id: str, reason: str = "") -> None:
    conn = connect(database_path)
    try:
        conn.execute(
            "UPDATE usage_reservations SET status='unknown', "
            "reference=COALESCE(reference, ?), updated_at=? WHERE id=? AND status='reserved'",
            (reason[:200] if reason else None, utcnow_iso(), reservation_id),
        )
    finally:
        conn.close()


def reconcile(
    database_path: str,
    reservation_id: str,
    *,
    status: str,
    cost_cents: int | None = None,
) -> dict[str, Any] | None:
    if status == "settled":
        return settle(database_path, reservation_id, cost_cents=cost_cents)
    if status == "released":
        return release(database_path, reservation_id, "admin reconcile")
    raise ValueError("status must be settled or released")


def pending_reservations(database_path: str, limit: int = 20) -> list[dict[str, Any]]:
    conn = connect(database_path)
    try:
        rows = conn.execute(
            "SELECT * FROM usage_reservations WHERE status='reserved' AND reference IS NOT NULL "
            "ORDER BY created_at ASC LIMIT ?",
            (max(1, min(limit, 100)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_reservations(database_path: str, limit: int = 200) -> list[dict[str, Any]]:
    conn = connect(database_path)
    try:
        rows = conn.execute(
            "SELECT * FROM usage_reservations ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 1000)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def summary(database_path: str) -> dict[str, Any]:
    conn = connect(database_path)
    try:
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        by_status = {
            row["status"]: row["cnt"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM usage_reservations GROUP BY status"
            ).fetchall()
        }
        by_kind = {
            row["quota_kind"]: row["cnt"]
            for row in conn.execute(
                "SELECT quota_kind, COUNT(*) AS cnt FROM usage_reservations GROUP BY quota_kind"
            ).fetchall()
        }
        settled_today = conn.execute(
            "SELECT COALESCE(SUM(settled_cost_cents),0) FROM usage_reservations "
            "WHERE status='settled' AND created_at>=?",
            (today,),
        ).fetchone()[0]
        reserved_today = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_cents),0) FROM usage_reservations "
            "WHERE status='reserved' AND created_at>=?",
            (today,),
        ).fetchone()[0]
        budget = int(get_setting_from_conn(conn, "daily_budget_cents"))
        return {
            "by_status": by_status,
            "by_kind": by_kind,
            "settled_cost_cents_today": int(settled_today),
            "reserved_cost_cents_today": int(reserved_today),
            "daily_budget_cents": budget,
            "remaining_budget_cents": (
                None if budget < 0 else max(0, budget - int(settled_today) - int(reserved_today))
            ),
        }
    finally:
        conn.close()


def grant(database_path: str, card_id: str, kind: str, units: int) -> dict[str, Any]:
    if kind not in KIND_COLUMNS:
        raise ValueError(f"unknown quota kind: {kind}")
    conn = connect(database_path)
    try:
        total_col, _, _ = KIND_COLUMNS[kind]
        conn.execute(
            f"UPDATE card_quotas SET {total_col}={total_col}+?, updated_at=? WHERE card_id=?",
            (int(units), utcnow_iso(), card_id),
        )
        row = conn.execute("SELECT * FROM card_quotas WHERE card_id=?", (card_id,)).fetchone()
        if row is None:
            raise KeyError("quota not found")
        return dict(row)
    finally:
        conn.close()
