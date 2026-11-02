from __future__ import annotations

import uuid

import pytest

from app import cards, gate_settings, plans, quotas
from app.db import init_db


def _active_card(settings, plan: str = "trial"):
    init_db(settings.database_path)
    plans.seed_plans(settings.database_path)
    gate_settings.seed_settings(settings.database_path, settings)
    _, codes = cards.create_batch(
        settings.database_path,
        name="quota",
        plan=plan,
        count=1,
        valid_days=7,
        pepper=settings.effective_card_pepper,
    )
    card = cards.find_by_code(settings.database_path, codes[0], settings.effective_card_pepper)
    username, credential_enc = cards.ensure_pending_identity(
        settings.database_path, card, credential_enc="encrypted"
    )
    cards.activate_card(
        settings.database_path,
        card=card,
        presenton_user_id=f"user-{uuid.uuid4().hex}",
        username=username,
        credential_enc=credential_enc,
        valid_days=7,
    )
    fresh = cards.find_by_code(settings.database_path, codes[0], settings.effective_card_pepper)
    quotas.ensure_quota(settings.database_path, fresh)
    return fresh


def test_reserve_settle_and_release(settings) -> None:
    card = _active_card(settings)
    reservation, duplicate = quotas.reserve(
        settings.database_path,
        card_id=card["id"],
        kind="generation",
        action="presentation.generate",
        units=1,
        estimated_cost_cents=20,
        request_key=None,
        settings=settings,
    )
    assert duplicate is False
    assert reservation["status"] == "reserved"
    quota = quotas.get_quota(settings.database_path, card["id"])
    assert quota["generations_reserved"] == 1

    settled = quotas.settle(settings.database_path, reservation["id"], cost_cents=20)
    assert settled is not None and settled["status"] == "settled"
    quota = quotas.get_quota(settings.database_path, card["id"])
    assert quota["generations_reserved"] == 0
    assert quota["generations_used"] == 1

    extra, _ = quotas.reserve(
        settings.database_path,
        card_id=card["id"],
        kind="generation",
        action="presentation.generate",
        units=1,
        estimated_cost_cents=20,
        request_key=None,
        settings=settings,
    )
    released = quotas.release(settings.database_path, extra["id"], "test")
    assert released is not None and released["status"] == "released"
    quota = quotas.get_quota(settings.database_path, card["id"])
    assert quota["generations_reserved"] == 0
    assert quota["generations_used"] == 1


def test_quota_exhaustion_and_budget(settings) -> None:
    init_db(settings.database_path)
    plans.seed_plans(settings.database_path)
    gate_settings.seed_settings(settings.database_path, settings)
    plans.upsert_plan(
        settings.database_path,
        {
            "code": "one",
            "name": "one",
            "valid_days": 7,
            "generations": 1,
            "ai_edits": 0,
            "image_generations": 0,
            "max_slides": 5,
            "price_cents": 0,
            "active": True,
        },
    )
    card = _active_card(settings, plan="one")
    reservation, _ = quotas.reserve(
        settings.database_path,
        card_id=card["id"],
        kind="generation",
        action="presentation.generate",
        units=1,
        estimated_cost_cents=20,
        request_key=None,
        settings=settings,
    )
    quotas.settle(settings.database_path, reservation["id"], cost_cents=20)

    with pytest.raises(quotas.QuotaExceeded):
        quotas.reserve(
            settings.database_path,
            card_id=card["id"],
            kind="generation",
            action="presentation.generate",
            units=1,
            estimated_cost_cents=20,
            request_key=None,
            settings=settings,
        )

    with pytest.raises(quotas.QuotaExceeded):
        quotas.reserve(
            settings.database_path,
            card_id=card["id"],
            kind="ai_edit",
            action="presentation.ai_edit",
            units=1,
            estimated_cost_cents=5,
            request_key=None,
            settings=settings,
        )

    budget_card = _active_card(settings, plan="one")
    gate_settings.set_setting(settings.database_path, "daily_budget_cents", 0)
    with pytest.raises(quotas.BudgetExceeded):
        quotas.reserve(
            settings.database_path,
            card_id=budget_card["id"],
            kind="generation",
            action="presentation.generate",
            units=1,
            estimated_cost_cents=20,
            request_key=None,
            settings=settings,
        )


def test_request_key_is_idempotent(settings) -> None:
    card = _active_card(settings)
    first, duplicate = quotas.reserve(
        settings.database_path,
        card_id=card["id"],
        kind="generation",
        action="presentation.generate",
        units=1,
        estimated_cost_cents=20,
        request_key="same-key",
        settings=settings,
    )
    second, duplicate2 = quotas.reserve(
        settings.database_path,
        card_id=card["id"],
        kind="generation",
        action="presentation.generate",
        units=1,
        estimated_cost_cents=20,
        request_key="same-key",
        settings=settings,
    )
    assert duplicate is False
    assert duplicate2 is True
    assert first["id"] == second["id"]
    quota = quotas.get_quota(settings.database_path, card["id"])
    assert quota["generations_reserved"] == 1
