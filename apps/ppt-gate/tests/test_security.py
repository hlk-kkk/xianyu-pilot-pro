from __future__ import annotations

import time

from app.security import (
    decrypt_secret,
    encrypt_secret,
    generate_card_code,
    hash_card_code,
    normalize_card_code,
    sign_session,
    verify_session,
)


def test_card_code_shape_and_normalization() -> None:
    code = generate_card_code()
    assert code.startswith("PPT-")
    normalized = normalize_card_code(code)
    assert len(normalized) == 23  # PPT + 20 random characters
    assert normalized.isalnum()


def test_card_hash_is_deterministic_and_pepper_scoped() -> None:
    code = "PPT-AAAA-BBBB-CCCC-DDDD-EEEE"
    assert hash_card_code(code, "pepper-one") == hash_card_code(code.lower(), "pepper-one")
    assert hash_card_code(code, "pepper-one") != hash_card_code(code, "pepper-two")


def test_session_sign_and_tamper() -> None:
    secret = "s" * 40
    token = sign_session({"card_id": "c1", "exp": int(time.time()) + 60}, secret)
    assert verify_session(token, secret) == {"card_id": "c1", "exp": verify_session(token, secret)["exp"]}
    assert verify_session(token + "x", secret) is None
    assert verify_session(token, "other" * 10) is None


def test_session_expiry() -> None:
    secret = "s" * 40
    token = sign_session({"card_id": "c1", "exp": int(time.time()) - 1}, secret)
    assert verify_session(token, secret) is None


def test_secret_encryption_roundtrip() -> None:
    secret = "e" * 40
    token = encrypt_secret("shadow-credential", secret)
    assert token != "shadow-credential"
    assert decrypt_secret(token, secret) == "shadow-credential"
