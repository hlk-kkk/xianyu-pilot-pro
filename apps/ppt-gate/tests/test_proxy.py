from __future__ import annotations

from datetime import timedelta

from app import cards
from app.db import connect, utcnow


def _redeem_one(settings, client):
    _, codes = cards.create_batch(
        settings.database_path,
        name="proxy-test",
        plan="trial",
        count=1,
        valid_days=7,
        pepper=settings.effective_card_pepper,
    )
    response = client.get(f"/r/{codes[0]}", follow_redirects=False)
    assert response.status_code == 303
    card = cards.find_by_code(settings.database_path, codes[0], settings.effective_card_pepper)
    return card


def test_proxy_forwards_only_presenton_cookie(settings, proxy_client) -> None:
    _redeem_one(settings, proxy_client)
    response = proxy_client.get("/api/v1/auth/verify")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert "presenton_session=" in body["cookie"]
    assert "ppt_gate_session=" not in body["cookie"]


def test_admin_login_and_mcp_paths_are_blocked(settings, proxy_client) -> None:
    _redeem_one(settings, proxy_client)
    assert proxy_client.get("/api/v1/admin/users").status_code == 403
    assert proxy_client.post("/api/v1/auth/login", json={}).status_code == 403
    assert proxy_client.post("/api/v1/auth/setup", json={}).status_code == 403
    assert proxy_client.get("/mcp").status_code == 403


def test_proxy_requires_gate_session(settings, proxy_client) -> None:
    proxy_client.cookies.clear()
    response = proxy_client.get("/api/v1/auth/verify")
    assert response.status_code == 401


def test_expired_card_allows_read_and_blocks_generation(settings, proxy_client) -> None:
    card = _redeem_one(settings, proxy_client)
    conn = connect(settings.database_path)
    try:
        conn.execute(
            "UPDATE cards SET expires_at=? WHERE id=?",
            ((utcnow() - timedelta(days=1)).isoformat(), card["id"]),
        )
    finally:
        conn.close()

    assert proxy_client.get("/api/v1/auth/verify").status_code == 200
    assert proxy_client.post("/api/v1/ppt/presentation/generate/async", json={}).status_code == 403
    export = proxy_client.post(
        "/api/v1/ppt/presentation/00000000-0000-0000-0000-000000000000/export"
    )
    assert export.status_code == 200


def test_missing_presenton_cookie_is_reissued(settings, proxy_client) -> None:
    _redeem_one(settings, proxy_client)
    proxy_client.cookies.delete("presenton_session")
    response = proxy_client.get("/api/v1/auth/verify")
    assert response.status_code == 200
    assert "presenton_session" in response.cookies
