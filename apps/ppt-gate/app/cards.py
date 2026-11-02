from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from .db import connect, parse_iso, utcnow, utcnow_iso
from .security import (
    card_prefix,
    generate_card_code,
    hash_card_code,
    random_credential,
    random_username,
)


ACTIVE_STATUSES = {"active"}
RETRYABLE_STATUSES = {"available", "provision_failed"}


def _card_dict(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def audit(
    database_path: str,
    actor: str,
    action: str,
    card_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    conn = connect(database_path)
    try:
        conn.execute(
            "INSERT INTO audit_log(actor, action, card_id, detail, created_at) "
            "VALUES(?,?,?,?,?)",
            (
                actor,
                action,
                card_id,
                json.dumps(detail or {}, ensure_ascii=False) if detail else None,
                utcnow_iso(),
            ),
        )
    finally:
        conn.close()


def create_batch(
    database_path: str,
    *,
    name: str,
    plan: str,
    count: int,
    valid_days: int,
    pepper: str,
) -> tuple[str, list[str]]:
    batch_id = "batch_" + uuid.uuid4().hex[:12]
    now = utcnow_iso()
    codes = [generate_card_code() for _ in range(count)]
    conn = connect(database_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO card_batches(id, name, plan, valid_days, total, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (batch_id, name, plan, valid_days, count, now),
        )
        for code in codes:
            conn.execute(
                "INSERT INTO cards("
                "id, batch_id, code_hash, code_prefix, plan, valid_days, status, "
                "created_at, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "card_" + uuid.uuid4().hex,
                    batch_id,
                    hash_card_code(code, pepper),
                    card_prefix(code),
                    plan,
                    valid_days,
                    "available",
                    now,
                    now,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    audit(database_path, "admin", "batch.created", None, {"batch_id": batch_id, "count": count, "plan": plan})
    return batch_id, codes


def get_card(database_path: str, card_id: str) -> dict[str, Any] | None:
    conn = connect(database_path)
    try:
        row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return _card_dict(row)
    finally:
        conn.close()


def find_by_code(database_path: str, code: str, pepper: str) -> dict[str, Any] | None:
    conn = connect(database_path)
    try:
        row = conn.execute(
            "SELECT * FROM cards WHERE code_hash=?", (hash_card_code(code, pepper),)
        ).fetchone()
        return _card_dict(row)
    finally:
        conn.close()


def claim_card(database_path: str, code: str, pepper: str) -> dict[str, Any] | None:
    """Atomically move an available card into provisioning.

    Returns the current card row. The caller must treat a ``provisioning``
    result as either owned by this request (if it was just claimed) or as a
    concurrent/stale claim.
    """
    code_hash = hash_card_code(code, pepper)
    conn = connect(database_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM cards WHERE code_hash=?", (code_hash,)).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        card = dict(row)
        now = utcnow_iso()
        if card["status"] in RETRYABLE_STATUSES:
            cursor = conn.execute(
                "UPDATE cards SET status='provisioning', updated_at=? "
                "WHERE id=? AND status IN ('available','provision_failed')",
                (now, card["id"]),
            )
            if cursor.rowcount == 1:
                conn.execute("COMMIT")
                card["status"] = "provisioning"
                card["_claimed"] = True
                return card
            conn.execute("ROLLBACK")
            return get_card(database_path, card["id"])
        if card["status"] == "provisioning":
            updated = parse_iso(card.get("updated_at"))
            if updated is not None and (utcnow() - updated).total_seconds() > 120:
                conn.execute(
                    "UPDATE cards SET updated_at=? WHERE id=? AND status='provisioning'",
                    (now, card["id"]),
                )
                conn.execute("COMMIT")
                return card
        conn.execute("COMMIT")
        return card
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def ensure_pending_identity(
    database_path: str,
    card: dict[str, Any],
    *,
    credential_enc: str,
) -> tuple[str, str]:
    """Return (username, encrypted credential) for a card.

    The identity is persisted before calling Presenton so a failed or
    interrupted provisioning can be retried with the same username.
    """
    username = card.get("presenton_username") or random_username("card")
    existing_enc = card.get("credential_enc") or credential_enc
    conn = connect(database_path)
    try:
        conn.execute(
            "UPDATE cards SET presenton_username=?, credential_enc=?, updated_at=? WHERE id=?",
            (username, existing_enc, utcnow_iso(), card["id"]),
        )
    finally:
        conn.close()
    return username, existing_enc


def activate_card(
    database_path: str,
    *,
    card: dict[str, Any],
    presenton_user_id: str,
    username: str,
    credential_enc: str,
    valid_days: int,
) -> None:
    now_dt = utcnow()
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(days=valid_days)).isoformat()
    conn = connect(database_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE cards SET status='active', presenton_user_id=?, presenton_username=?, "
            "credential_enc=?, redeemed_at=COALESCE(redeemed_at, ?), expires_at=?, "
            "last_error=NULL, updated_at=? WHERE id=?",
            (presenton_user_id, username, credential_enc, now, expires_at, now, card["id"]),
        )
        conn.execute(
            "INSERT INTO presenton_users(username, presenton_user_id, card_id, credential_enc, created_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET presenton_user_id=excluded.presenton_user_id, "
            "credential_enc=excluded.credential_enc",
            (username, presenton_user_id, card["id"], credential_enc, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    audit(database_path, "buyer", "card.activated", card["id"], {"username": username})


def release_provision(database_path: str, card_id: str, error: str) -> None:
    conn = connect(database_path)
    try:
        row = conn.execute("SELECT presenton_user_id FROM cards WHERE id=?", (card_id,)).fetchone()
        has_user = bool(row and row["presenton_user_id"])
        conn.execute(
            "UPDATE cards SET status=?, last_error=?, updated_at=? WHERE id=?",
            ("provision_failed" if has_user else "available", error[:500], utcnow_iso(), card_id),
        )
    finally:
        conn.close()
    audit(database_path, "system", "card.provision_failed", card_id, {"error": error[:200]})


def revoke_card(database_path: str, card_id: str, reason: str = "") -> bool:
    conn = connect(database_path)
    try:
        cursor = conn.execute(
            "UPDATE cards SET status='revoked', last_error=?, updated_at=? WHERE id=? AND status!='revoked'",
            (reason[:500] or None, utcnow_iso(), card_id),
        )
        changed = cursor.rowcount > 0
    finally:
        conn.close()
    if changed:
        audit(database_path, "admin", "card.revoked", card_id, {"reason": reason})
    return changed


def card_is_expired(card: dict[str, Any]) -> bool:
    expires_at = parse_iso(card.get("expires_at"))
    return expires_at is not None and expires_at < utcnow()


def list_cards(database_path: str, limit: int = 200) -> list[dict[str, Any]]:
    conn = connect(database_path)
    try:
        rows = conn.execute(
            "SELECT id, batch_id, code_prefix, plan, status, presenton_user_id, "
            "presenton_username, expires_at, redeemed_at, last_error, created_at, updated_at "
            "FROM cards ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 1000)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def card_credential(card: dict[str, Any]) -> str:
    return card.get("credential_enc") or ""


def new_credential_enc(secret: str) -> str:
    from .security import encrypt_secret

    return encrypt_secret(random_credential(), secret)


def get_batch(database_path: str, batch_id: str) -> dict[str, Any] | None:
    conn = connect(database_path)
    try:
        row = conn.execute(
            "SELECT * FROM card_batches WHERE id=?", (batch_id,)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def update_batch_import(
    database_path: str,
    batch_id: str,
    *,
    group_id: int | None,
    status: str,
    imported_count: int,
    error: str | None = None,
) -> None:
    conn = connect(database_path)
    try:
        conn.execute(
            "UPDATE card_batches SET xianyu_group_id=COALESCE(?, xianyu_group_id), "
            "xianyu_import_status=?, xianyu_imported_count=?, xianyu_import_error=?, "
            "xianyu_imported_at=? WHERE id=?",
            (
                group_id,
                status,
                int(imported_count),
                error[:500] if error else None,
                utcnow_iso(),
                batch_id,
            ),
        )
    finally:
        conn.close()
    audit(
        database_path,
        "admin",
        "batch.xianyu_import",
        None,
        {
            "batch_id": batch_id,
            "group_id": group_id,
            "status": status,
            "imported_count": imported_count,
            "error": error,
        },
    )
