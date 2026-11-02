from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


CARD_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CARD_BODY_LENGTH = 20


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def normalize_card_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def generate_card_code() -> str:
    body = "".join(secrets.choice(CARD_ALPHABET) for _ in range(CARD_BODY_LENGTH))
    groups = [body[index : index + 4] for index in range(0, CARD_BODY_LENGTH, 4)]
    return "PPT-" + "-".join(groups)


def card_prefix(code: str) -> str:
    normalized = normalize_card_code(code)
    if normalized.startswith("PPT"):
        normalized = normalized[3:]
    return normalized[:6]


def hash_card_code(code: str, pepper: str) -> str:
    payload = normalize_card_code(code).encode("utf-8")
    return hmac.new(pepper.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def random_credential() -> str:
    return secrets.token_urlsafe(24)


def random_username(prefix: str = "u") -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(value: str, secret: str) -> str:
    return _fernet(secret).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, secret: str) -> str:
    try:
        return _fernet(secret).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("encrypted secret cannot be decrypted") from exc


def sign_session(payload: dict[str, Any], secret: str) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return f"{_b64encode(body)}.{_b64encode(signature)}"


def verify_session(token: str, secret: str) -> dict[str, Any] | None:
    try:
        body_part, signature_part = token.split(".", 1)
        body = _b64decode(body_part)
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(signature_part)):
            return None
        payload = json.loads(body)
        expires_at = int(payload.get("exp", 0))
        if expires_at and expires_at < int(time.time()):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
