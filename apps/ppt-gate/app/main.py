from __future__ import annotations

import asyncio
import contextlib
import hmac
import html
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import cards as card_store
from . import gate_settings, metering, plans, quotas
from .config import Settings, get_settings
from .db import init_db
from .presenton import PresentonClient, PresentonError
from .proxy import forward_buffered, forward_request
from .security import (
    constant_time_equal,
    decrypt_secret,
    encrypt_secret,
    random_credential,
)
from .settlement import SettlementWorker
from .sessions import (
    clear_auth_cookies,
    issue_gate_session,
    read_gate_session,
    set_gate_cookie,
    set_presenton_cookie,
)
from .xianyu import XianyuClient, XianyuError


logger = logging.getLogger("ppt_gate")
CARD_CODE_PATTERN = re.compile(r"^[A-Za-z0-9-]{4,64}$")
EXPIRED_ALLOWED_POST = re.compile(
    r"^/api/v1/ppt/presentation/[0-9a-fA-F-]{36}/export$"
)
BLOCKED_PREFIXES = (
    "/api/v1/admin/",
    "/mcp",
    "/api/v1/auth/setup",
    "/api/v1/auth/login",
)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class BatchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    plan: str = Field(default="trial", min_length=1, max_length=40)
    count: int = Field(default=1, ge=1, le=1000)
    valid_days: int | None = Field(default=None, ge=1, le=365)
    import_to_xianyu: bool = False
    xianyu_group_id: int | None = None
    xianyu_group_name: str | None = None


class PlanRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    valid_days: int = Field(ge=1, le=3650)
    generations: int = Field(ge=-1, le=100000)
    ai_edits: int = Field(ge=-1, le=100000)
    image_generations: int = Field(default=0, ge=-1, le=100000)
    max_slides: int = Field(ge=1, le=50)
    price_cents: int = Field(default=0, ge=0)
    active: bool = True


class GrantRequest(BaseModel):
    kind: Literal["generation", "ai_edit", "image"]
    units: int = Field(ge=1, le=100000)


class ReconcileRequest(BaseModel):
    status: Literal["settled", "released"]
    cost_cents: int | None = Field(default=None, ge=0)


class SettingsRequest(BaseModel):
    daily_budget_cents: int | None = Field(default=None, ge=-1)
    estimated_generation_cents: int | None = Field(default=None, ge=0)
    estimated_ai_edit_cents: int | None = Field(default=None, ge=0)
    estimated_image_cents: int | None = Field(default=None, ge=0)
    max_concurrent_generations: int | None = Field(default=None, ge=-1)
    max_concurrent_generations_per_card: int | None = Field(default=None, ge=-1)
    metering_enabled: bool | None = None
    deny_unknown_writes: bool | None = None


def _page(title: str, message: str, status_code: int = 200) -> HTMLResponse:
    body = f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f6f7fb;color:#1f2937;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#fff;border-radius:16px;padding:32px 28px;max-width:520px;box-shadow:0 10px 40px rgba(15,23,42,.08)}}
h1{{font-size:22px;margin:0 0 12px}}p{{line-height:1.7;color:#4b5563}}a{{color:#4f46e5}}
</style></head>
<body><div class="card"><h1>{title}</h1><p>{message}</p></div></body></html>"""
    return HTMLResponse(
        body,
        status_code=status_code,
        headers={"cache-control": "no-store", "referrer-policy": "no-referrer"},
    )


def _admin_ok(request: Request, settings: Settings) -> bool:
    if not settings.gate_admin_token:
        return False
    supplied = request.headers.get("x-admin-token", "")
    return constant_time_equal(supplied, settings.gate_admin_token)


def _secure_request(request: Request, settings: Settings) -> bool:
    if settings.cookie_secure:
        return True
    return (request.headers.get("x-forwarded-proto") or request.url.scheme).lower() == "https"


def _request_base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "127.0.0.1"
    return f"{proto}://{host}".rstrip("/")


def _apply_cookie_headers(response, cookie_holder: RedirectResponse | None) -> None:
    if cookie_holder is None:
        return
    for value in cookie_holder.headers.getlist("set-cookie"):
        response.headers.append("set-cookie", value)


def create_app(
    settings: Settings | None = None,
    *,
    presenton_client: PresentonClient | None = None,
    proxy_client: httpx.AsyncClient | None = None,
    xianyu_client: XianyuClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    init_db(settings.database_path)
    plans.seed_plans(settings.database_path)
    gate_settings.seed_settings(settings.database_path, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.presenton = presenton_client or PresentonClient(
            settings.presenton_base_url,
            settings.presenton_admin_username,
            settings.presenton_admin_secret,
            timeout=settings.presenton_timeout_seconds,
        )
        app.state.proxy_client = proxy_client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=settings.proxy_read_timeout_seconds,
                write=settings.proxy_read_timeout_seconds,
                pool=None,
            ),
            follow_redirects=False,
        )
        if xianyu_client is not None:
            app.state.xianyu = xianyu_client
        elif settings.xianyu_api_base_url and settings.xianyu_username and settings.xianyu_secret:
            app.state.xianyu = XianyuClient(
                settings.xianyu_api_base_url,
                settings.xianyu_username,
                settings.xianyu_secret,
                timeout=settings.xianyu_timeout_seconds,
            )
        else:
            app.state.xianyu = None

        settlement = SettlementWorker(app)
        app.state.settlement = settlement
        settlement_task = asyncio.create_task(settlement.run())
        try:
            yield
        finally:
            await settlement.stop()
            settlement_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await settlement_task
            if presenton_client is None:
                await app.state.presenton.close()
            if proxy_client is None:
                await app.state.proxy_client.aclose()
            if xianyu_client is None and app.state.xianyu is not None:
                await app.state.xianyu.close()

    app = FastAPI(title="PPT Gate", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": "ppt-gate", "version": "0.2.0"}

    @app.post("/admin/cards/batch")
    async def create_batch(body: BatchRequest, request: Request) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        plan = plans.get_plan_or_default(settings.database_path, body.plan)
        valid_days = body.valid_days or int(plan["valid_days"])
        batch_id, codes = card_store.create_batch(
            settings.database_path,
            name=body.name,
            plan=str(plan["code"]),
            count=body.count,
            valid_days=valid_days,
            pepper=settings.effective_card_pepper,
        )
        base = _request_base_url(request)
        cards = [
            {"code": code, "redeem_url": f"{base}/r/{code}"} for code in codes
        ]
        xianyu_result: dict[str, Any] | None = None
        should_import = bool(
            body.import_to_xianyu
            or body.xianyu_group_id
            or (settings.xianyu_import_enabled and settings.xianyu_card_group_id)
            or (settings.xianyu_import_enabled and body.xianyu_group_name)
        )
        if should_import:
            xianyu_result = await _import_batch_to_xianyu(
                app,
                settings,
                batch_id=batch_id,
                cards=cards,
                body=body,
            )
        return JSONResponse(
            {
                "batch_id": batch_id,
                "plan": plan["code"],
                "valid_days": valid_days,
                "count": len(codes),
                "cards": cards,
                "xianyu": xianyu_result,
            },
            headers={"cache-control": "no-store"},
        )

    async def _import_batch_to_xianyu(
        app: FastAPI,
        settings: Settings,
        *,
        batch_id: str,
        cards: list[dict[str, Any]],
        body: BatchRequest,
    ) -> dict[str, Any]:
        client: XianyuClient | None = app.state.xianyu
        if client is None:
            card_store.update_batch_import(
                settings.database_path,
                batch_id,
                group_id=body.xianyu_group_id,
                status="failed",
                imported_count=0,
                error="Xianyu client is not configured",
            )
            return {"status": "failed", "error": "Xianyu client is not configured"}
        group_id = body.xianyu_group_id or settings.xianyu_card_group_id
        group_name = body.xianyu_group_name or settings.xianyu_card_group_name
        try:
            if group_id is None:
                if not group_name:
                    raise XianyuError("未配置闲鱼卡密组 ID 或名称")
                group_id = await client.ensure_group(group_name)
            result = await client.batch_import(
                int(group_id), [item["redeem_url"] for item in cards]
            )
            fail = int(result.get("failCount", 0))
            status = "success" if fail == 0 else "partial"
            card_store.update_batch_import(
                settings.database_path,
                batch_id,
                group_id=group_id,
                status=status,
                imported_count=int(result.get("successCount", 0)),
            )
            return {"status": status, "group_id": group_id, **result}
        except XianyuError as exc:
            logger.warning("xianyu import failed batch=%s error=%s", batch_id, exc)
            card_store.update_batch_import(
                settings.database_path,
                batch_id,
                group_id=group_id,
                status="failed",
                imported_count=0,
                error=str(exc),
            )
            return {"status": "failed", "group_id": group_id, "error": str(exc)}

    @app.get("/admin/cards")
    async def list_cards(request: Request, limit: int = 200) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        return JSONResponse({"cards": card_store.list_cards(settings.database_path, limit)})

    @app.post("/admin/cards/{card_id}/revoke")
    async def revoke_card(card_id: str, request: Request) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        changed = card_store.revoke_card(settings.database_path, card_id, "admin revoke")
        if not changed:
            raise HTTPException(status_code=404, detail="card not found")
        return JSONResponse({"ok": True, "card_id": card_id})

    @app.get("/admin/cards/{card_id}/quota")
    async def card_quota(card_id: str, request: Request) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        card = card_store.get_card(settings.database_path, card_id)
        if card is None:
            raise HTTPException(status_code=404, detail="card not found")
        quota = quotas.ensure_quota(settings.database_path, card)
        return JSONResponse({"card_id": card_id, "quota": quota})

    @app.post("/admin/cards/{card_id}/grant")
    async def grant_quota(card_id: str, body: GrantRequest, request: Request) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        card = card_store.get_card(settings.database_path, card_id)
        if card is None:
            raise HTTPException(status_code=404, detail="card not found")
        quotas.ensure_quota(settings.database_path, card)
        quota = quotas.grant(settings.database_path, card_id, body.kind, body.units)
        return JSONResponse({"card_id": card_id, "quota": quota})

    @app.get("/admin/plans")
    async def list_plans(request: Request) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        return JSONResponse({"plans": plans.list_plans(settings.database_path)})

    @app.put("/admin/plans/{code}")
    async def upsert_plan(code: str, body: PlanRequest, request: Request) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        payload = body.model_dump()
        payload["code"] = code
        return JSONResponse({"plan": plans.upsert_plan(settings.database_path, payload)})

    @app.get("/admin/settings")
    async def get_settings_admin(request: Request) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        return JSONResponse({"settings": gate_settings.all_settings(settings.database_path)})

    @app.put("/admin/settings")
    async def update_settings_admin(body: SettingsRequest, request: Request) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        for key, value in body.model_dump(exclude_none=True).items():
            gate_settings.set_setting(settings.database_path, key, value)
        return JSONResponse({"settings": gate_settings.all_settings(settings.database_path)})

    @app.get("/admin/usage")
    async def list_usage(request: Request, limit: int = 200) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        return JSONResponse({"reservations": quotas.list_reservations(settings.database_path, limit)})

    @app.get("/admin/usage/summary")
    async def usage_summary(request: Request) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        return JSONResponse(quotas.summary(settings.database_path))

    @app.post("/admin/reservations/{reservation_id}/reconcile")
    async def reconcile_reservation(
        reservation_id: str, body: ReconcileRequest, request: Request
    ) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        result = quotas.reconcile(
            settings.database_path,
            reservation_id,
            status=body.status,
            cost_cents=body.cost_cents,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="reservation not found")
        return JSONResponse({"reservation": result})

    @app.get("/admin/xianyu/groups")
    async def xianyu_groups(request: Request) -> JSONResponse:
        if not _admin_ok(request, settings):
            raise HTTPException(status_code=401, detail="admin token required")
        client: XianyuClient | None = app.state.xianyu
        if client is None:
            raise HTTPException(status_code=503, detail="Xianyu client is not configured")
        try:
            return JSONResponse({"groups": await client.list_groups()})
        except XianyuError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def _login_and_redirect(
        card: dict[str, Any],
        *,
        username: str,
        credential_enc: str,
        request: Request,
    ) -> HTMLResponse | RedirectResponse:
        credential = decrypt_secret(credential_enc, settings.gate_secret)
        presenton = app.state.presenton
        try:
            presenton_cookie = await presenton.login_user(username, credential)
        except PresentonError as exc:
            logger.warning(
                "presenton login failed card=%s codePrefix=%s error=%s",
                card["id"],
                card.get("code_prefix"),
                exc,
            )
            return _page(
                "登录失败",
                "作品空间暂时无法登录，请稍后刷新重试。如持续失败，请联系卖家。",
                status_code=502,
            )
        gate_token = issue_gate_session(
            card_id=card["id"],
            presenton_user_id=card["presenton_user_id"],
            username=username,
            secret=settings.gate_secret,
            ttl_seconds=settings.session_ttl_seconds,
        )
        if settings.redeem_landing_enabled:
            code_prefix = html.escape(str(card.get("code_prefix") or ""))
            response: HTMLResponse | RedirectResponse = HTMLResponse(
                f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>兑换成功</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f6f7fb;color:#1f2937;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#fff;border-radius:16px;padding:32px 28px;max-width:540px;box-shadow:0 10px 40px rgba(15,23,42,.08)}}
h1{{font-size:22px;margin:0 0 12px}}p,li{{line-height:1.7;color:#4b5563}}a.button{{display:inline-block;margin-top:14px;background:#4f46e5;color:#fff;text-decoration:none;border-radius:10px;padding:10px 18px}}
.meta{{font-size:12px;color:#9ca3af;margin-top:18px}}code{{font-size:12px}}
.fallback{{margin-top:16px;padding:12px 14px;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px}}
</style></head>
<body><div class="card">
<h1>兑换成功</h1>
<p id="status">正在验证登录状态，请稍候…</p>
<p>如果页面没有自动进入工作台，请点击下方按钮。</p>
<a class="button" href="/">进入工作台</a>
<div id="fallback" class="fallback" hidden>
<p><strong>浏览器没有保存登录会话。</strong>请按以下步骤操作：</p>
<ol><li>改用 Safari 或 Chrome 打开本链接；</li><li>清除 ppt.linjitech.com 的站点数据；</li><li>重新打开兑换链接。</li></ol>
</div>
<p class="meta">兑换码前缀：<code>{code_prefix}</code></p>
</div>
<script>
(async function () {{
  var fallback = document.getElementById('fallback');
  var status = document.getElementById('status');
  try {{
    var controller = new AbortController();
    var timer = setTimeout(function () {{ controller.abort(); }}, 5000);
    var response = await fetch('/api/v1/auth/verify', {{
      credentials: 'include', cache: 'no-store', signal: controller.signal,
      headers: {{'Accept': 'application/json'}}
    }});
    clearTimeout(timer);
    if (response.ok) {{ location.replace('/'); return; }}
  }} catch (error) {{}}
  status.textContent = '登录会话未保存，请按下方步骤操作。';
  fallback.hidden = false;
}})();
</script>
</body></html>""",
                status_code=200,
            )
        else:
            response = RedirectResponse("/", status_code=303)
        secure = _secure_request(request, settings)
        set_gate_cookie(
            response,
            gate_token,
            secure=secure,
            domain=settings.cookie_domain,
            max_age=settings.session_ttl_seconds,
        )
        set_presenton_cookie(
            response,
            presenton_cookie,
            secure=secure,
            domain=settings.cookie_domain,
            max_age=settings.session_ttl_seconds,
        )
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["cache-control"] = "no-store"
        logger.info(
            "兑换登录成功: cardId=%s codePrefix=%s presentonUser=%s landing=%s",
            card["id"],
            card.get("code_prefix"),
            username,
            settings.redeem_landing_enabled,
        )
        return response

    async def _provision(card: dict[str, Any], *, request: Request) -> HTMLResponse | RedirectResponse:
        presenton = app.state.presenton
        credential_enc = card.get("credential_enc") or encrypt_secret(
            random_credential(), settings.gate_secret
        )
        username, credential_enc = card_store.ensure_pending_identity(
            settings.database_path, card, credential_enc=credential_enc
        )
        credential = decrypt_secret(credential_enc, settings.gate_secret)
        try:
            user = await presenton.create_user(username, credential)
            card_store.activate_card(
                settings.database_path,
                card=card,
                presenton_user_id=user.id,
                username=user.username,
                credential_enc=credential_enc,
                valid_days=int(card.get("valid_days") or settings.card_default_valid_days),
            )
        except PresentonError as exc:
            card_store.release_provision(settings.database_path, card["id"], str(exc))
            logger.warning("presenton provisioning failed card=%s error=%s", card["id"], exc)
            return _page(
                "开通失败",
                "兑换码已确认，但作品空间开通失败。请刷新重试；如果连续失败，请联系卖家。",
                status_code=502,
            )
        fresh = card_store.get_card(settings.database_path, card["id"]) or card
        quotas.ensure_quota(settings.database_path, fresh)
        return await _login_and_redirect(
            fresh,
            username=user.username,
            credential_enc=credential_enc,
            request=request,
        )

    @app.get("/r/{code}")
    async def redeem(code: str, request: Request):
        if not CARD_CODE_PATTERN.fullmatch(code):
            return _page("兑换码无效", "兑换码格式不正确，请检查后重试。", status_code=404)
        card = card_store.find_by_code(
            settings.database_path, code, settings.effective_card_pepper
        )
        if card is None:
            return _page("兑换码无效", "没有找到这个兑换码，请确认是否输入正确。", status_code=404)
        if card["status"] == "revoked":
            return _page("兑换码已停用", "该兑换码已被停用，请联系卖家。", status_code=403)
        if card["status"] == "active":
            quotas.ensure_quota(settings.database_path, card)
            return await _login_and_redirect(
                card,
                username=card["presenton_username"],
                credential_enc=card["credential_enc"],
                request=request,
            )

        card = card_store.claim_card(
            settings.database_path, code, settings.effective_card_pepper
        )
        if card is None:
            return _page("兑换码无效", "没有找到这个兑换码，请确认是否输入正确。", status_code=404)
        if card["status"] == "active":
            quotas.ensure_quota(settings.database_path, card)
            return await _login_and_redirect(
                card,
                username=card["presenton_username"],
                credential_enc=card["credential_enc"],
                request=request,
            )
        if card["status"] == "provisioning":
            if card.get("_claimed") or card.get("presenton_username"):
                return await _provision(card, request=request)
            return _page(
                "正在开通",
                "这个兑换码正在开通作品空间，请 5 秒后刷新页面。",
                status_code=409,
            )
        return _page("兑换码不可用", "该兑换码当前不可用，请联系卖家。", status_code=409)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy(path: str, request: Request):
        full_path = "/" + path.lstrip("/")
        for prefix in BLOCKED_PREFIXES:
            if full_path.startswith(prefix):
                return JSONResponse({"detail": "forbidden"}, status_code=403)

        session = read_gate_session(request, settings.gate_secret)
        if session is None:
            if full_path in {"/api/v1/auth/status"}:
                return JSONResponse({"configured": True, "authenticated": False})
            return _page(
                "需要兑换码",
                "请先使用闲鱼订单收到的兑换链接进入作品空间。",
                status_code=401,
            )

        card = card_store.get_card(settings.database_path, str(session.get("card_id")))
        if card is None or card["status"] != "active":
            return _page("兑换码不可用", "该兑换码已失效，请联系卖家。", status_code=403)

        if card_store.card_is_expired(card):
            if request.method not in {"GET", "HEAD"} and not EXPIRED_ALLOWED_POST.match(full_path):
                return JSONResponse(
                    {"detail": "兑换码已过期，无法继续生成或编辑"},
                    status_code=403,
                )

        body: bytes | None = None
        action = (
            metering.classify(request.method, full_path)
            if bool(gate_settings.get_setting(settings.database_path, "metering_enabled"))
            else None
        )
        if action is not None:
            if action.needs_body and request.method not in {"GET", "HEAD"}:
                body = await request.body()
                if len(body) > settings.max_metered_body_bytes:
                    return JSONResponse({"detail": "请求体过大"}, status_code=413)
            quota = quotas.ensure_quota(settings.database_path, card)
            if action.kind == "generation":
                requested_slides = metering.extract_n_slides(body)
                max_slides = int(quota.get("max_slides") or 0)
                if requested_slides is not None and max_slides > 0 and requested_slides > max_slides:
                    return JSONResponse(
                        {"detail": f"该套餐单份最多 {max_slides} 页", "max_slides": max_slides},
                        status_code=400,
                    )
            estimated_cost = quotas.estimate_cost(settings.database_path, action.kind)
            try:
                reservation, duplicate = quotas.reserve(
                    settings.database_path,
                    card_id=card["id"],
                    kind=action.kind,
                    action=action.action,
                    units=1,
                    estimated_cost_cents=estimated_cost,
                    request_key=metering.request_key(request.headers),
                    settings=settings,
                )
            except quotas.QuotaExceeded as exc:
                return JSONResponse({"detail": str(exc)}, status_code=403)
            except (quotas.BudgetExceeded, quotas.ConcurrencyExceeded) as exc:
                return JSONResponse({"detail": str(exc)}, status_code=429)
            if duplicate:
                return JSONResponse(
                    {"detail": "重复请求", "reservation_id": reservation["id"]},
                    status_code=409,
                )
        else:
            if (
                request.method in WRITE_METHODS
                and not metering.is_free_write(request.method, full_path)
                and bool(gate_settings.get_setting(settings.database_path, "deny_unknown_writes"))
            ):
                return JSONResponse({"detail": "未授权的写入路径"}, status_code=403)

        presenton_cookie = request.cookies.get("presenton_session", "")
        new_cookie_response: RedirectResponse | None = None
        if not presenton_cookie:
            try:
                credential = decrypt_secret(card["credential_enc"], settings.gate_secret)
                presenton_cookie = await app.state.presenton.login_user(
                    card["presenton_username"], credential
                )
                new_cookie_response = RedirectResponse("/", status_code=307)
                set_presenton_cookie(
                    new_cookie_response,
                    presenton_cookie,
                    secure=_secure_request(request, settings),
                    domain=settings.cookie_domain,
                    max_age=settings.session_ttl_seconds,
                )
            except (ValueError, PresentonError) as exc:
                if action is not None:
                    quotas.release(settings.database_path, reservation["id"], "presenton login failed")
                logger.warning("presenton cookie refresh failed card=%s error=%s", card["id"], exc)
                return _page("登录已失效", "作品空间登录已失效，请重新打开兑换链接。", status_code=401)

        try:
            if action is not None and action.async_task:
                response, payload = await forward_buffered(
                    request,
                    client=app.state.proxy_client,
                    presenton_base_url=settings.presenton_base_url,
                    path=path,
                    presenton_cookie=presenton_cookie,
                    body=body,
                )
                if 200 <= response.status_code < 300:
                    task_id = metering.task_id_from_response(payload)
                    if task_id:
                        quotas.attach_reference(settings.database_path, reservation["id"], task_id)
                    else:
                        quotas.mark_unknown(
                            settings.database_path,
                            reservation["id"],
                            "async response had no task id",
                        )
                else:
                    quotas.release(
                        settings.database_path,
                        reservation["id"],
                        f"upstream {response.status_code}",
                    )
            else:
                response = await forward_request(
                    request,
                    client=app.state.proxy_client,
                    presenton_base_url=settings.presenton_base_url,
                    path=path,
                    presenton_cookie=presenton_cookie,
                    body=body,
                )
                if action is not None:
                    if 200 <= response.status_code < 300:
                        quotas.settle(
                            settings.database_path,
                            reservation["id"],
                            cost_cents=int(reservation["estimated_cost_cents"]),
                        )
                    else:
                        quotas.release(
                            settings.database_path,
                            reservation["id"],
                            f"upstream {response.status_code}",
                        )
        except httpx.HTTPError as exc:
            if action is not None:
                quotas.release(settings.database_path, reservation["id"], f"proxy error: {exc}")
            logger.warning("proxy error path=%s error=%s", full_path, exc)
            return JSONResponse({"detail": "upstream unavailable"}, status_code=502)

        _apply_cookie_headers(response, new_cookie_response)
        return response

    @app.exception_handler(PresentonError)
    async def presenton_error_handler(_: Request, exc: PresentonError) -> JSONResponse:
        logger.warning("presenton error: %s", exc)
        return JSONResponse({"detail": "presenton unavailable"}, status_code=502)

    return app
