from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.config import Settings
from app.main import create_app
from app.presenton import PresentonError, PresentonUser
from app.xianyu import XianyuClient


class FakePresenton:
    def __init__(self) -> None:
        self.users: dict[str, tuple[str, str]] = {}
        self.tasks: dict[str, str] = {}

    async def create_user(self, username: str, credential: str) -> PresentonUser:
        if username not in self.users:
            self.users[username] = (credential, f"id-{username}")
        return PresentonUser(id=self.users[username][1], username=username)

    async def find_user(self, username: str) -> PresentonUser | None:
        record = self.users.get(username)
        return PresentonUser(id=record[1], username=username) if record else None

    async def login_user(self, username: str, credential: str) -> str:
        record = self.users.get(username)
        if record is None or record[0] != credential:
            raise PresentonError("invalid credentials")
        return f"session-{username}"

    async def verify(self, cookie: str) -> dict | None:
        return {"authenticated": True} if cookie else None

    async def async_task_status(self, cookie: str, task_id: str) -> tuple[int, dict | None]:
        if not cookie:
            return 401, None
        return 200, {"id": task_id, "status": self.tasks.get(task_id, "pending")}

    async def close(self) -> None:
        return None


class FakeXianyu:
    def __init__(self) -> None:
        self.groups: dict[int, str] = {7: "AI PPT 兑换码"}
        self.imported: list[str] = []

    async def list_groups(self, keyword: str = "") -> list[dict]:
        return [
            {"id": group_id, "groupName": name}
            for group_id, name in self.groups.items()
            if not keyword or keyword in name
        ]

    async def ensure_group(self, name: str, description: str = "") -> int:
        for group_id, group_name in self.groups.items():
            if group_name == name:
                return group_id
        group_id = max(self.groups) + 1
        self.groups[group_id] = name
        return group_id

    async def batch_import(self, group_id: int, contents) -> dict[str, int]:
        items = list(contents)
        self.imported.extend(items)
        return {"successCount": len(items), "duplicateCount": 0, "failCount": 0}

    async def close(self) -> None:
        return None


async def _verify(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "authenticated": True,
            "username": "stub",
            "method": "jwt",
            "cookie": request.headers.get("cookie", ""),
        }
    )


async def _generate(request: Request) -> JSONResponse:
    return JSONResponse({"path": "/app_data/stub.pptx", "edit_path": "/presentation?id=1"})


async def _generate_async(request: Request) -> JSONResponse:
    return JSONResponse({"id": "task-stub", "status": "pending"})


async def _edit(request: Request) -> JSONResponse:
    return JSONResponse({"path": "/app_data/edit.pptx"})


async def _echo(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "path": request.url.path,
            "method": request.method,
            "cookie": request.headers.get("cookie", ""),
        }
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_path=str(tmp_path / "gate.db"),
        gate_secret="g" * 40,
        card_pepper="p" * 40,
        gate_admin_token="a" * 40,
        presenton_admin_username="admin",
        presenton_admin_secret="s" * 40,
        public_base_url="http://testserver",
        settlement_interval_seconds=3600,
        # Existing redeem tests assert the classic 303 behavior.
        redeem_landing_enabled=False,
    )


@pytest.fixture
def fake_presenton() -> FakePresenton:
    return FakePresenton()


@pytest.fixture
def fake_xianyu() -> FakeXianyu:
    return FakeXianyu()


@pytest.fixture
def stub_upstream() -> Starlette:
    return Starlette(
        routes=[
            Route("/api/v1/auth/verify", _verify),
            Route("/api/v1/ppt/presentation/generate/async", _generate_async, methods=["POST"]),
            Route("/api/v1/ppt/presentation/generate", _generate, methods=["POST"]),
            Route("/api/v1/ppt/presentation/edit", _edit, methods=["POST"]),
            Route(
                "/{path:path}",
                _echo,
                methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
            ),
        ]
    )


@pytest.fixture
def client(settings: Settings, fake_presenton: FakePresenton):
    app = create_app(settings, presenton_client=fake_presenton)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def landing_client(settings: Settings, fake_presenton: FakePresenton):
    landing_settings = settings.model_copy(
        update={"redeem_landing_enabled": True}
    )
    app = create_app(landing_settings, presenton_client=fake_presenton)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def proxy_client(settings: Settings, fake_presenton: FakePresenton, stub_upstream: Starlette):
    upstream = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stub_upstream),
        base_url="http://upstream",
    )
    app = create_app(
        settings,
        presenton_client=fake_presenton,
        proxy_client=upstream,
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        import asyncio

        asyncio.run(upstream.aclose())


@pytest.fixture
def xianyu_proxy_client(settings: Settings, fake_presenton: FakePresenton, fake_xianyu: FakeXianyu, stub_upstream: Starlette):
    upstream = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stub_upstream),
        base_url="http://upstream",
    )
    app = create_app(
        settings,
        presenton_client=fake_presenton,
        proxy_client=upstream,
        xianyu_client=fake_xianyu,
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        import asyncio

        asyncio.run(upstream.aclose())


@pytest.fixture
def real_xianyu_client():
    async def _login(request: Request) -> JSONResponse:
        return JSONResponse({"code": 200, "msg": "ok", "data": {"token": "user-token"}})

    async def _list(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "code": 200,
                "msg": "ok",
                "data": {"records": [{"id": 7, "groupName": "AI PPT 兑换码"}], "total": 1},
            }
        )

    async def _create(request: Request) -> JSONResponse:
        return JSONResponse({"code": 200, "msg": "ok", "data": 8})

    async def _import(request: Request) -> JSONResponse:
        body = await request.json()
        count = len(body.get("items") or [])
        return JSONResponse(
            {
                "code": 200,
                "msg": "ok",
                "data": {"successCount": count, "duplicateCount": 0, "failCount": 0},
            }
        )

    stub = Starlette(
        routes=[
            Route("/api/login/login", _login, methods=["POST"]),
            Route("/api/cards", _list, methods=["GET"]),
            Route("/api/cards", _create, methods=["POST"]),
            Route("/api/cards/{group_id}/items/batch", _import, methods=["POST"]),
        ]
    )
    async_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stub),
        base_url="http://xianyu",
    )
    client = XianyuClient(
        "http://xianyu",
        "service-user",
        "service-secret",
        client=async_client,
    )
    yield client, async_client
