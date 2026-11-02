from __future__ import annotations

import asyncio

from app import cards, gate_settings, plans, quotas


def _custom_plan(settings, *, generations: int = 2, ai_edits: int = 5, max_slides: int = 10):
    plans.upsert_plan(
        settings.database_path,
        {
            "code": "metered",
            "name": "metered",
            "valid_days": 7,
            "generations": generations,
            "ai_edits": ai_edits,
            "image_generations": 0,
            "max_slides": max_slides,
            "price_cents": 0,
            "active": True,
        },
    )


def _issue(settings, client, plan: str = "metered") -> dict:
    _, codes = cards.create_batch(
        settings.database_path,
        name="metering",
        plan=plan,
        count=1,
        valid_days=7,
        pepper=settings.effective_card_pepper,
    )
    response = client.get(f"/r/{codes[0]}", follow_redirects=False)
    assert response.status_code == 303
    return cards.find_by_code(settings.database_path, codes[0], settings.effective_card_pepper)


def test_sync_generation_charges_quota_then_blocks(settings, proxy_client) -> None:
    _custom_plan(settings, generations=1)
    card = _issue(settings, proxy_client)
    first = proxy_client.post(
        "/api/v1/ppt/presentation/generate",
        json={"content": "测试", "n_slides": 3},
    )
    assert first.status_code == 200
    reservations = quotas.list_reservations(settings.database_path)
    assert len(reservations) == 1
    assert reservations[0]["status"] == "settled"
    quota = quotas.get_quota(settings.database_path, card["id"])
    assert quota["generations_used"] == 1
    assert quota["generations_reserved"] == 0

    second = proxy_client.post(
        "/api/v1/ppt/presentation/generate",
        json={"content": "测试", "n_slides": 3},
    )
    assert second.status_code == 403


def test_slide_limit_is_enforced_before_forwarding(settings, proxy_client) -> None:
    _custom_plan(settings, max_slides=5)
    _issue(settings, proxy_client)
    response = proxy_client.post(
        "/api/v1/ppt/presentation/generate",
        json={"content": "测试", "n_slides": 6},
    )
    assert response.status_code == 400
    assert quotas.list_reservations(settings.database_path) == []


def test_budget_breaker(settings, proxy_client) -> None:
    _custom_plan(settings, generations=2)
    _issue(settings, proxy_client)
    gate_settings.set_setting(settings.database_path, "daily_budget_cents", 0)
    response = proxy_client.post(
        "/api/v1/ppt/presentation/generate",
        json={"content": "测试", "n_slides": 3},
    )
    assert response.status_code == 429
    assert quotas.list_reservations(settings.database_path) == []


def test_ai_edit_quota(settings, proxy_client) -> None:
    _custom_plan(settings, ai_edits=0)
    _issue(settings, proxy_client)
    response = proxy_client.post(
        "/api/v1/ppt/presentation/edit",
        json={"presentation_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 403


def test_async_generation_is_reserved_then_settled(settings, proxy_client, fake_presenton) -> None:
    _custom_plan(settings, generations=2)
    card = _issue(settings, proxy_client)
    response = proxy_client.post(
        "/api/v1/ppt/presentation/generate/async",
        json={"content": "测试", "n_slides": 3},
    )
    assert response.status_code == 200
    assert response.json()["id"] == "task-stub"
    reservations = quotas.list_reservations(settings.database_path)
    assert len(reservations) == 1
    assert reservations[0]["status"] == "reserved"
    assert reservations[0]["reference"] == "task-stub"

    fake_presenton.tasks["task-stub"] = "completed"
    asyncio.run(proxy_client.app.state.settlement.tick(force=True))

    reservations = quotas.list_reservations(settings.database_path)
    assert reservations[0]["status"] == "settled"
    quota = quotas.get_quota(settings.database_path, card["id"])
    assert quota["generations_used"] == 1


def test_duplicate_request_key_returns_409(settings, proxy_client) -> None:
    _custom_plan(settings, generations=2)
    _issue(settings, proxy_client)
    headers = {"Idempotency-Key": "fixed-key"}
    first = proxy_client.post(
        "/api/v1/ppt/presentation/generate",
        json={"content": "测试", "n_slides": 3},
        headers=headers,
    )
    assert first.status_code == 200
    second = proxy_client.post(
        "/api/v1/ppt/presentation/generate",
        json={"content": "测试", "n_slides": 3},
        headers=headers,
    )
    assert second.status_code == 409
    assert len(quotas.list_reservations(settings.database_path)) == 1
