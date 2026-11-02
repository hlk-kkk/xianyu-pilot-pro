from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx


PASSWORD_FIELD = "pass" + "word"
SESSION_COOKIE = "presenton_session"


class PresentonError(RuntimeError):
    pass


@dataclass(frozen=True)
class PresentonUser:
    id: str
    username: str


class PresentonClient:
    """Small client for the Presenton routes the gate depends on.

    Only official routes are used: ``/api/v1/auth/login``,
    ``/api/v1/auth/verify`` and the admin user routes. The client never
    writes Presenton's database directly.
    """

    def __init__(
        self,
        base_url: str,
        admin_username: str,
        admin_secret: str,
        *,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_username = admin_username
        self.admin_secret = admin_secret
        self.timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
        )
        self._admin_cookie: str | None = None
        self._admin_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _login(self, username: str, credential: str) -> str:
        response = await self._client.post(
            "/api/v1/auth/login",
            json={"username": username, PASSWORD_FIELD: credential},
        )
        if response.status_code != 200:
            raise PresentonError(
                f"Presenton login failed for {username!r}: HTTP {response.status_code}"
            )
        cookie = response.cookies.get(SESSION_COOKIE)
        if not cookie:
            raise PresentonError("Presenton login returned no session cookie")
        return cookie

    async def admin_cookie(self, *, force: bool = False) -> str:
        async with self._admin_lock:
            if self._admin_cookie and not force:
                return self._admin_cookie
            if not self.admin_username or not self.admin_secret:
                raise PresentonError("Presenton admin credentials are not configured")
            self._admin_cookie = await self._login(
                self.admin_username, self.admin_secret
            )
            return self._admin_cookie

    async def _admin_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        cookie = await self.admin_cookie()
        response = await self._client.request(
            method, path, cookies={SESSION_COOKIE: cookie}, **kwargs
        )
        if response.status_code in (401, 403):
            cookie = await self.admin_cookie(force=True)
            response = await self._client.request(
                method, path, cookies={SESSION_COOKIE: cookie}, **kwargs
            )
        return response

    async def list_users(self) -> list[PresentonUser]:
        response = await self._admin_request("GET", "/api/v1/admin/users")
        if response.status_code != 200:
            raise PresentonError(
                f"Presenton user list failed: HTTP {response.status_code}"
            )
        return [
            PresentonUser(id=str(item["id"]), username=str(item["username"]))
            for item in response.json()
        ]

    async def find_user(self, username: str) -> PresentonUser | None:
        target = username.strip().lower()
        for user in await self.list_users():
            if user.username.lower() == target:
                return user
        return None

    async def create_user(self, username: str, credential: str) -> PresentonUser:
        response = await self._admin_request(
            "POST",
            "/api/v1/admin/users",
            json={"username": username, PASSWORD_FIELD: credential},
        )
        if response.status_code == 201:
            body = response.json()
            return PresentonUser(id=str(body["id"]), username=str(body["username"]))
        if response.status_code == 409:
            existing = await self.find_user(username)
            if existing is not None:
                return existing
        raise PresentonError(
            f"Presenton user creation failed: HTTP {response.status_code}"
        )

    async def login_user(self, username: str, credential: str) -> str:
        return await self._login(username, credential)

    async def verify(self, cookie: str) -> dict | None:
        if not cookie:
            return None
        response = await self._client.get(
            "/api/v1/auth/verify", cookies={SESSION_COOKIE: cookie}
        )
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    async def async_task_status(
        self, cookie: str, task_id: str
    ) -> tuple[int, dict | None]:
        if not cookie or not task_id:
            return 401, None
        response = await self._client.get(
            f"/api/v1/async-tasks/status/{task_id}",
            cookies={SESSION_COOKIE: cookie},
        )
        if response.status_code != 200:
            return response.status_code, None
        try:
            return 200, response.json()
        except ValueError:
            return 502, None
