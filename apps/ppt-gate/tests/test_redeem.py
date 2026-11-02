from __future__ import annotations

from app import cards


def _issue(settings, count: int = 1):
    return cards.create_batch(
        settings.database_path,
        name="test",
        plan="trial",
        count=count,
        valid_days=7,
        pepper=settings.effective_card_pepper,
    )


def test_redeem_sets_cookies_and_activates_card(settings, client) -> None:
    _, codes = _issue(settings)
    response = client.get(f"/r/{codes[0]}", follow_redirects=False)
    assert response.status_code == 303
    assert "ppt_gate_session" in client.cookies
    assert "presenton_session" in client.cookies

    card = cards.find_by_code(settings.database_path, codes[0], settings.effective_card_pepper)
    assert card["status"] == "active"
    assert card["presenton_user_id"]
    assert card["presenton_username"]


def test_redeem_is_idempotent(settings, client) -> None:
    _, codes = _issue(settings)
    first = client.get(f"/r/{codes[0]}", follow_redirects=False)
    assert first.status_code == 303
    card = cards.find_by_code(settings.database_path, codes[0], settings.effective_card_pepper)
    username = card["presenton_username"]

    second = client.get(f"/r/{codes[0]}", follow_redirects=False)
    assert second.status_code == 303
    card = cards.find_by_code(settings.database_path, codes[0], settings.effective_card_pepper)
    assert card["presenton_username"] == username


def test_unknown_code_returns_404(settings, client) -> None:
    response = client.get("/r/PPT-AAAA-BBBB-CCCC-DDDD-EEEE", follow_redirects=False)
    assert response.status_code == 404


def test_revoked_code_is_rejected(settings, client) -> None:
    _, codes = _issue(settings)
    client.get(f"/r/{codes[0]}", follow_redirects=False)
    card = cards.find_by_code(settings.database_path, codes[0], settings.effective_card_pepper)
    cards.revoke_card(settings.database_path, card["id"])
    response = client.get(f"/r/{codes[0]}", follow_redirects=False)
    assert response.status_code == 403
