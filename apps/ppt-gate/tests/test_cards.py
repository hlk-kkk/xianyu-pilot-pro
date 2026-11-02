from __future__ import annotations

from app import cards
from app.db import init_db


def test_batch_and_atomic_claim(settings) -> None:
    init_db(settings.database_path)
    batch_id, codes = cards.create_batch(
        settings.database_path,
        name="test",
        plan="trial",
        count=1,
        valid_days=7,
        pepper=settings.effective_card_pepper,
    )
    code = codes[0]
    assert batch_id.startswith("batch_")

    found = cards.find_by_code(settings.database_path, code, settings.effective_card_pepper)
    assert found is not None
    assert found["status"] == "available"

    first = cards.claim_card(settings.database_path, code, settings.effective_card_pepper)
    assert first is not None
    assert first["status"] == "provisioning"

    second = cards.claim_card(settings.database_path, code, settings.effective_card_pepper)
    assert second is not None
    assert second["status"] == "provisioning"

    username, credential_enc = cards.ensure_pending_identity(
        settings.database_path, first, credential_enc="encrypted"
    )
    cards.activate_card(
        settings.database_path,
        card=first,
        presenton_user_id="user-1",
        username=username,
        credential_enc=credential_enc,
        valid_days=7,
    )
    active = cards.find_by_code(settings.database_path, code, settings.effective_card_pepper)
    assert active["status"] == "active"
    assert active["presenton_user_id"] == "user-1"
    assert not cards.card_is_expired(active)

    assert cards.revoke_card(settings.database_path, active["id"]) is True
    revoked = cards.find_by_code(settings.database_path, code, settings.effective_card_pepper)
    assert revoked["status"] == "revoked"


def test_release_provision_returns_card_to_available(settings) -> None:
    init_db(settings.database_path)
    _, codes = cards.create_batch(
        settings.database_path,
        name="retry",
        plan="trial",
        count=1,
        valid_days=7,
        pepper=settings.effective_card_pepper,
    )
    claimed = cards.claim_card(settings.database_path, codes[0], settings.effective_card_pepper)
    cards.release_provision(settings.database_path, claimed["id"], "boom")
    found = cards.find_by_code(settings.database_path, codes[0], settings.effective_card_pepper)
    assert found["status"] == "available"
    assert found["last_error"] == "boom"
