from __future__ import annotations

import time

from fastapi import Request, Response

from .security import sign_session, verify_session


GATE_COOKIE = "ppt_gate_session"


def issue_gate_session(
    *,
    card_id: str,
    presenton_user_id: str,
    username: str,
    secret: str,
    ttl_seconds: int,
) -> str:
    now = int(time.time())
    return sign_session(
        {
            "card_id": card_id,
            "user_id": presenton_user_id,
            "username": username,
            "iat": now,
            "exp": now + max(60, ttl_seconds),
        },
        secret,
    )


def read_gate_session(request: Request, secret: str) -> dict | None:
    token = request.cookies.get(GATE_COOKIE)
    if not token:
        return None
    return verify_session(token, secret)


def set_gate_cookie(
    response: Response,
    token: str,
    *,
    secure: bool,
    domain: str | None,
    max_age: int,
) -> None:
    response.set_cookie(
        GATE_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        domain=domain,
    )


def set_presenton_cookie(
    response: Response,
    token: str,
    *,
    secure: bool,
    domain: str | None,
    max_age: int,
) -> None:
    response.set_cookie(
        "presenton_session",
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        domain=domain,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(GATE_COOKIE, path="/")
    response.delete_cookie("presenton_session", path="/")
