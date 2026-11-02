from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MeteredAction:
    kind: str
    action: str
    async_task: bool = False
    needs_body: bool = True


GENERATION_PATHS = {
    "/api/v1/ppt/presentation/generate",
    "/api/v2/ppt/presentation/generate",
    "/api/v1/ppt/presentation/create",
}
ASYNC_GENERATION_PATHS = {
    "/api/v1/ppt/presentation/generate/async",
    "/api/v2/ppt/presentation/generate/async",
}
AI_EDIT_PATHS = {
    "/api/v1/ppt/presentation/edit",
    "/api/v1/ppt/presentation/derive",
    "/api/v1/ppt/theme/generate",
    "/api/v1/ppt/chat/message",
    "/api/v1/ppt/chat/message/stream",
}
IMAGE_PATHS = {
    "/api/v1/ppt/images/generate",
}
FREE_WRITE_EXACT = {
    "/api/v1/ppt/presentation/update",
    "/api/v1/ppt/presentation/slide_update",
    "/api/v1/ppt/presentation/prepare",
    "/api/v1/ppt/presentation/create/blank",
    "/api/v1/ppt/images/upload",
    "/api/v1/ppt/files",
    "/api/v1/auth/logout",
}
FREE_WRITE_PATTERNS = (
    re.compile(r"^/api/v1/ppt/presentation/[0-9a-fA-F-]{36}/(duplicate|export)$"),
    re.compile(r"^/api/v1/ppt/presentation/status/[^/]+$"),
    re.compile(r"^/api/v1/async-tasks(/status/[^/]+)?$"),
    re.compile(r"^/api/v1/ppt/files/"),
    re.compile(r"^/api/v1/ppt/fonts/"),
)


def _normalize(path: str) -> str:
    return (path or "/").split("?", 1)[0]


def classify(method: str, path: str) -> MeteredAction | None:
    normalized = _normalize(path)
    upper = method.upper()
    if upper == "GET":
        if normalized in IMAGE_PATHS:
            return MeteredAction("image", "image.generate", needs_body=False)
        return None
    if upper not in {"POST", "PUT", "PATCH"}:
        return None
    if normalized in ASYNC_GENERATION_PATHS:
        return MeteredAction("generation", "presentation.generate.async", async_task=True)
    if normalized in GENERATION_PATHS:
        return MeteredAction("generation", "presentation.generate")
    if normalized in AI_EDIT_PATHS or normalized.startswith("/api/v1/ppt/chat/"):
        return MeteredAction("ai_edit", "presentation.ai_edit")
    return None


def is_free_write(method: str, path: str) -> bool:
    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return True
    normalized = _normalize(path)
    if normalized in FREE_WRITE_EXACT:
        return True
    return any(pattern.match(normalized) for pattern in FREE_WRITE_PATTERNS)


def extract_n_slides(body: bytes | None) -> int | None:
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("n_slides")
    if isinstance(raw, int) and raw > 0:
        return raw
    markdown = payload.get("slides_markdown")
    if isinstance(markdown, list) and markdown:
        return len(markdown)
    return None


def request_key(headers) -> str | None:
    for name in ("idempotency-key", "x-request-key"):
        value = headers.get(name)
        if value:
            trimmed = value.strip()
            if trimmed:
                return trimmed[:128]
    return None


def task_id_from_response(payload: bytes | None) -> str | None:
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        for key in ("id", "task_id"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("id", "task_id"):
                value = nested.get(key)
                if isinstance(value, str) and value:
                    return value
    return None
