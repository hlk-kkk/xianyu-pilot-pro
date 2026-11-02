from __future__ import annotations

from typing import AsyncIterator

import httpx
from fastapi import Request
from starlette.responses import Response, StreamingResponse


HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _upstream_url(base_url: str, path: str, query: str) -> str:
    base = base_url.rstrip("/")
    target = "/" + path.lstrip("/") if path else "/"
    url = base + target
    if query:
        url += "?" + query
    return url


def _request_headers(request: Request, presenton_cookie: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lower = key.lower()
        if lower in HOP_BY_HOP or lower in {"host", "cookie"}:
            continue
        headers[key] = value

    cookies: list[str] = []
    if presenton_cookie:
        cookies.append(f"presenton_session={presenton_cookie}")
    for key, value in request.cookies.items():
        if key in {"ppt_gate_session", "presenton_session"}:
            continue
        cookies.append(f"{key}={value}")
    if cookies:
        headers["cookie"] = "; ".join(cookies)

    forwarded_proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    headers["x-forwarded-proto"] = forwarded_proto
    headers["x-forwarded-host"] = request.headers.get("host", "")
    if request.client is not None:
        headers["x-forwarded-for"] = request.client.host
    return headers


def _body_iterator(request: Request, body: bytes | None) -> AsyncIterator[bytes] | bytes | None:
    if body is not None:
        return body
    if request.method in {"GET", "HEAD"}:
        return None
    return request.stream()


async def _stream_upstream(upstream: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in upstream.aiter_raw():
            yield chunk
    finally:
        await upstream.aclose()


async def forward_request(
    request: Request,
    *,
    client: httpx.AsyncClient,
    presenton_base_url: str,
    path: str,
    presenton_cookie: str,
    body: bytes | None = None,
) -> StreamingResponse:
    url = _upstream_url(presenton_base_url, path, request.url.query)
    headers = _request_headers(request, presenton_cookie)
    upstream_request = client.build_request(
        request.method,
        url,
        headers=headers,
        content=_body_iterator(request, body),
    )
    upstream = await client.send(upstream_request, stream=True)

    response = StreamingResponse(
        _stream_upstream(upstream),
        status_code=upstream.status_code,
    )
    for key, value in upstream.headers.multi_items():
        if key.lower() in HOP_BY_HOP:
            continue
        response.headers.append(key, value)
    response.headers["referrer-policy"] = "no-referrer"
    return response


async def forward_buffered(
    request: Request,
    *,
    client: httpx.AsyncClient,
    presenton_base_url: str,
    path: str,
    presenton_cookie: str,
    body: bytes | None = None,
) -> tuple[Response, bytes]:
    """Forward a request and buffer the response body.

    Used for metered async endpoints where the gate must read the task id from
    a small JSON response before returning it to the browser.
    """
    url = _upstream_url(presenton_base_url, path, request.url.query)
    headers = _request_headers(request, presenton_cookie)
    upstream_request = client.build_request(
        request.method,
        url,
        headers=headers,
        content=_body_iterator(request, body),
    )
    upstream = await client.send(upstream_request)
    try:
        payload = await upstream.aread()
        response = Response(content=payload, status_code=upstream.status_code)
        for key, value in upstream.headers.multi_items():
            lower = key.lower()
            if lower in HOP_BY_HOP or lower in {"content-length", "content-encoding"}:
                continue
            response.headers.append(key, value)
        response.headers["referrer-policy"] = "no-referrer"
        return response, payload
    finally:
        await upstream.aclose()
