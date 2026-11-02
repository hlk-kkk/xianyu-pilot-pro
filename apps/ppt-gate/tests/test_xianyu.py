from __future__ import annotations

import asyncio

import httpx
import pytest

from app import cards
from app.xianyu import XianyuClient, XianyuError


def test_xianyu_client_group_and_import(real_xianyu_client) -> None:
    client, async_client = real_xianyu_client

    async def run() -> None:
        groups = await client.list_groups()
        assert groups[0]["id"] == 7
        assert await client.ensure_group("AI PPT 兑换码") == 7
        assert await client.ensure_group("新分组") == 8
        result = await client.batch_import(7, ["https://a", "https://b"])
        assert result == {"successCount": 2, "duplicateCount": 0, "failCount": 0}

    asyncio.run(run())
    asyncio.run(async_client.aclose())


def test_xianyu_network_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async def run() -> None:
        async_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://xianyu",
        )
        client = XianyuClient(
            "http://xianyu", "service-user", "service-secret", client=async_client
        )
        with pytest.raises(XianyuError):
            await client.list_groups()
        await async_client.aclose()

    asyncio.run(run())


def test_batch_endpoint_imports_to_xianyu(xianyu_proxy_client, fake_xianyu, settings) -> None:
    response = xianyu_proxy_client.post(
        "/admin/cards/batch",
        headers={"X-Admin-Token": "a" * 40},
        json={
            "name": "xianyu-test",
            "plan": "trial",
            "count": 2,
            "import_to_xianyu": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["xianyu"]["status"] == "success"
    assert body["xianyu"]["successCount"] == 2
    assert len(fake_xianyu.imported) == 2
    assert all("/r/" in url for url in fake_xianyu.imported)
    batch = cards.get_batch(settings.database_path, body["batch_id"])
    assert batch is not None
    assert batch["xianyu_import_status"] == "success"
    assert batch["xianyu_imported_count"] == 2
