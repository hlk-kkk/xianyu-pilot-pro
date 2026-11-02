from __future__ import annotations

from app import cards


def _issue(settings, count: int = 1):
    return cards.create_batch(
        settings.database_path,
        name="landing-test",
        plan="trial",
        count=count,
        valid_days=7,
        pepper=settings.effective_card_pepper,
    )


def test_landing_returns_200_and_sets_cookies(settings, landing_client) -> None:
    _, codes = _issue(settings)
    code = codes[0]
    response = landing_client.get(f"/r/{code}", follow_redirects=False)

    assert response.status_code == 200
    assert "进入工作台" in response.text
    assert "/api/v1/auth/verify" in response.text
    assert "Safari" in response.text
    assert "清除 ppt.linjitech.com 的站点数据" in response.text
    assert "ppt_gate_session" in landing_client.cookies
    assert "presenton_session" in landing_client.cookies
    # The landing page must not expose the full redeem code.
    assert code not in response.text


def test_landing_reentry_maps_to_same_presenton_user(settings, landing_client) -> None:
    _, codes = _issue(settings)
    first = landing_client.get(f"/r/{codes[0]}", follow_redirects=False)
    assert first.status_code == 200
    card = cards.find_by_code(
        settings.database_path, codes[0], settings.effective_card_pepper
    )
    username = card["presenton_username"]
    user_id = card["presenton_user_id"]

    second = landing_client.get(f"/r/{codes[0]}", follow_redirects=False)
    assert second.status_code == 200
    card = cards.find_by_code(
        settings.database_path, codes[0], settings.effective_card_pepper
    )
    assert card["presenton_username"] == username
    assert card["presenton_user_id"] == user_id


def test_landing_invalid_code_returns_404(settings, landing_client) -> None:
    response = landing_client.get(
        "/r/PPT-AAAA-BBBB-CCCC-DDDD-EEEE", follow_redirects=False
    )
    assert response.status_code == 404


def test_landing_revoked_code_returns_403(settings, landing_client) -> None:
    _, codes = _issue(settings)
    landing_client.get(f"/r/{codes[0]}", follow_redirects=False)
    card = cards.find_by_code(
        settings.database_path, codes[0], settings.effective_card_pepper
    )
    cards.revoke_card(settings.database_path, card["id"])

    response = landing_client.get(f"/r/{codes[0]}", follow_redirects=False)
    assert response.status_code == 403


def test_landing_disabled_returns_303(settings, client) -> None:
    _, codes = _issue(settings)
    response = client.get(f"/r/{codes[0]}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers.get("location") == "/"
