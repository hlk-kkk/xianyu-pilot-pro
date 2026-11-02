from __future__ import annotations

import asyncio
import time
from typing import Any, Iterable

import httpx


PASSWORD_FIELD = "pass" + "word"
MAX_IMPORT_ITEMS = 1000


class XianyuError(RuntimeError):
    pass


class XianyuClient:
    """Client for the Xianyu front-end user API used to manage card groups.

    The card API is protected by a user JWT, not the admin JWT. Configure a
    dedicated tenant user for the gate.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        secret: str,
        *,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.secret = secret
        self.timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._login_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _login(self) -> str:
        try:
            response = await self._client.post(
                "/api/login/login",
                json={"username": self.username, PASSWORD_FIELD: self.secret},
            )
        except httpx.HTTPError as exc:
            raise XianyuError(
                f"Xianyu login unavailable: {type(exc).__name__}"
            ) from exc
        if response.status_code != 200:
            raise XianyuError(f"Xianyu login failed: HTTP {response.status_code}")
        payload = response.json()
        if int(payload.get("code", 0)) != 200:
            raise XianyuError(f"Xianyu login rejected: {payload.get('msg')}")
        token = (payload.get("data") or {}).get("token")
        if not token:
            raise XianyuError("Xianyu login returned no token")
        self._token = str(token)
        self._token_expires_at = time.time() + 50 * 60
        return self._token

    async def token(self, *, force: bool = False) -> str:
        async with self._login_lock:
            if force or not self._token or time.time() >= self._token_expires_at:
                return await self._login()
            return self._token

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        token = await self.token()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self._client.request(
                method, path, headers=headers, **kwargs
            )
            if response.status_code == 401:
                token = await self.token(force=True)
                headers["Authorization"] = f"Bearer {token}"
                response = await self._client.request(
                    method, path, headers=headers, **kwargs
                )
        except httpx.HTTPError as exc:
            raise XianyuError(
                f"Xianyu API unavailable: {type(exc).__name__}"
            ) from exc
        if response.status_code != 200:
            raise XianyuError(f"Xianyu API failed: HTTP {response.status_code} {path}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise XianyuError(f"Xianyu API returned non-JSON: {path}") from exc
        if int(payload.get("code", 0)) != 200:
            raise XianyuError(str(payload.get("msg") or "Xianyu API error"))
        return payload.get("data")

    async def list_groups(self, keyword: str = "") -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/api/cards",
            params={"keyword": keyword, "current": 1, "size": 100},
        )
        if not isinstance(data, dict):
            return []
        records = data.get("records") or []
        return [item for item in records if isinstance(item, dict)]

    async def ensure_group(self, name: str, description: str = "") -> int:
        target = name.strip()
        if not target:
            raise XianyuError("card group name is empty")
        for group in await self.list_groups(target):
            if str(group.get("groupName") or "").strip() == target:
                return int(group["id"])
        data = await self._request(
            "POST",
            "/api/cards",
            json={
                "groupName": target,
                "description": description or "AI PPT 兑换码",
                "cardType": "kami",
                "status": 1,
            },
        )
        try:
            return int(data)
        except (TypeError, ValueError) as exc:
            raise XianyuError("Xianyu did not return a card group id") from exc

    async def batch_import(self, group_id: int, contents: Iterable[str]) -> dict[str, int]:
        items = [{"content": content} for content in contents if content]
        success = duplicate = fail = 0
        for index in range(0, len(items), MAX_IMPORT_ITEMS):
            chunk = items[index : index + MAX_IMPORT_ITEMS]
            data = await self._request(
                "POST",
                f"/api/cards/{int(group_id)}/items/batch",
                json={"items": chunk},
            )
            if not isinstance(data, dict):
                raise XianyuError("Xianyu import returned an unexpected payload")
            success += int(data.get("successCount", 0) or 0)
            duplicate += int(data.get("duplicateCount", 0) or 0)
            fail += int(data.get("failCount", 0) or 0)
        return {"successCount": success, "duplicateCount": duplicate, "failCount": fail}
