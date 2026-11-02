# ppt-gate（阶段 1 + 2）

闲鱼 AI PPT 自助工作台的门禁服务。当前实现：

1. 兑换码生成、原子核销、Presenton 用户映射
2. 兑换后免二次登录，自动下发 Presenton 会话
3. 反向代理 Presenton，拦截管理端路径
4. 按套餐对生成 / AI 改稿 / 生图计次，异步任务完成后结算
5. 每日预算熔断与并发限制
6. 把兑换链接批量导入闲鱼卡密组

## 架构

```text
浏览器
  │ http://127.0.0.1:8009
  ▼
ppt-gate (FastAPI + SQLite)
  ├─ /r/{code}        核销、创建/映射 Presenton 用户、登录、下发 Cookie
  ├─ /admin/*         兑换码、套餐、额度、用量、预算、闲鱼导入
  └─ /*               鉴权 + 计次后反向代理到 Presenton 127.0.0.1:5001
```

Presenton 必须只监听 `127.0.0.1`，不能直接暴露给买家。

## 计次规则

| 动作 | Presenton 路径 | 额度类型 |
|---|---|---|
| 同步生成 | `POST /api/v1/ppt/presentation/generate`、`/api/v2/...`、`/create` | generation |
| 异步生成 | `POST /api/v1/ppt/presentation/generate/async`、`/api/v2/...` | generation，任务完成后结算 |
| AI 改稿 / 对话 | `POST /presentation/edit`、`/derive`、`/ppt/chat/*`、`/ppt/theme/generate` | ai_edit |
| 生图 | `GET /api/v1/ppt/images/generate` | image |
| 导出 / 手工保存 / 上传 | `/{id}/export`、`/presentation/update`、`/slide_update`、`/images/upload` 等 | 免费 |

兑换入口默认返回 **200 落地页**：成功核销后设置 `ppt_gate_session` 与 `presenton_session`，页面会调用 `/api/v1/auth/verify` 检查 Cookie 是否生效；生效则自动进入工作台，未生效则提示改用 Safari/Chrome 并清除站点数据。设置 `REDEEM_LANDING_ENABLED=false` 可回滚到旧的 303 跳转行为。

流程：

1. 代理前检查兑换码有效期、套餐额度、单份页数、并发和每日预算；
2. 原子写入 `usage_reservations`，状态 `reserved`；
3. 同步请求在 2xx 后 `settled`，非 2xx 释放；
4. 异步生成从响应里取 task id，由后台 worker 轮询 `/api/v1/async-tasks/status/{id}`：
   - completed → settled
   - error/failed → released
   - 超过预留 TTL 仍未完成 → `unknown`，保留额度，等待管理员对账
5. 计费金额是**预估成本**，不是供应商账单；用于预算熔断，不对外承诺精确成本。

套餐默认值：

| 套餐 | 生成 | AI 改稿 | 生图 | 最大页数 | 有效期 |
|---|---:|---:|---:|---:|---:|
| trial | 2 | 5 | 0 | 10 | 7 天 |
| basic | 10 | 30 | 0 | 20 | 30 天 |
| pro | 30 | 100 | 20 | 40 | 30 天 |

套餐可通过 `PUT /admin/plans/{code}` 调整；已有兑换码使用激活时的额度快照，不受后续套餐修改影响。

## 闲鱼卡密组导入

使用**前台租户用户 API**，不是管理员 API：

- 登录：`POST /api/login/login`
- 批量导入：`POST /api/cards/{groupId}/items/batch`
- 卡密内容为兑换链接 `https://<gate>/r/PPT-...`

配置：

```bash
XIANYU_IMPORT_ENABLED=true
XIANYU_API_BASE_URL=http://127.0.0.1:18080
XIANYU_USERNAME=<专用租户用户>
XIANYU_SECRET=<该用户密码>
XIANYU_CARD_GROUP_ID=<已有卡密组 ID，可留空>
XIANYU_CARD_GROUP_NAME=AI PPT 兑换码
```

在闲鱼后台创建一个启用的 `kami` 卡密组，或者让门禁按名称自动创建。批量创建时传：

```json
{
  "name": "xianyu-batch-1",
  "plan": "trial",
  "count": 20,
  "import_to_xianyu": true,
  "xianyu_group_id": 12
}
```

返回的 `xianyu` 字段包含 `status`、`successCount`、`duplicateCount`、`failCount`、`group_id`。导入失败时兑换码仍会返回，可手工补录；批次状态写入 `card_batches`。

## 管理接口

所有 `/admin/*` 需要 `X-Admin-Token`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/admin/cards/batch` | 生成兑换码，可选导入闲鱼 |
| GET | `/admin/cards` | 卡密状态 |
| POST | `/admin/cards/{id}/revoke` | 封禁 |
| GET | `/admin/cards/{id}/quota` | 额度 |
| POST | `/admin/cards/{id}/grant` | 追加额度 |
| GET | `/admin/plans` | 套餐列表 |
| PUT | `/admin/plans/{code}` | 新建/更新套餐 |
| GET | `/admin/settings` | 预算与并发配置 |
| PUT | `/admin/settings` | 修改预算与并发配置 |
| GET | `/admin/usage` | 用量预留/结算明细 |
| GET | `/admin/usage/summary` | 今日成本、预算余额、状态汇总 |
| POST | `/admin/reservations/{id}/reconcile` | 对 `unknown` 任务手工结算/释放 |
| GET | `/admin/xianyu/groups` | 列出闲鱼卡密组 |
| GET | `/health` | 健康检查 |

## 安装与启动

```bash
cd apps/ppt-gate
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`：

- Presenton 管理员凭据（阶段 0 的凭据在 `/Users/hulk/presenton_data/phase0-credentials.json`，不要提交）。
- `GATE_SECRET`、`CARD_PEPPER`、`GATE_ADMIN_TOKEN`：各至少 32 位随机值。
- 闲鱼导入所需的专用租户用户凭据。

生成随机值：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

启动（关闭访问日志，避免兑换码进入 URL 日志）：

```bash
.venv/bin/uvicorn app.asgi:app --host 127.0.0.1 --port 8009 --no-access-log
```

生成兑换码（CLI 不直接导入闲鱼，导入请用管理接口）：

```bash
.venv/bin/python -m app.cli issue --name xianyu-batch-1 --plan trial --count 5
```

## 安全边界

- 兑换码只存 HMAC 哈希；原文只在生成时显示一次。
- 影子用户密码用 `GATE_SECRET` 派生 Fernet 密钥加密存储。
- 买家禁止访问 `/api/v1/admin/*`、`/mcp`、`/api/v1/auth/login`、`/api/v1/auth/setup`。
- 代理不把 `ppt_gate_session` 转发给 Presenton。
- 生产环境：`APP_ENV=production`、`COOKIE_SECURE=true`、所有密钥 ≥32 位，HTTPS 终止在门禁前。
- `GATE_SECRET` / `CARD_PEPPER` 变更会导致存量会话、加密凭据或兑换码失效；必须备份并保持稳定。

## 测试

```bash
.venv/bin/pytest -q
```

覆盖：兑换码/会话/加密、原子核销、重复兑换、封禁、代理 Cookie 边界、过期读写、套餐额度、预算熔断、并发限制、异步结算、幂等请求、闲鱼卡密组导入。

## 尚未覆盖

- 真实模型 token 账单对接：当前用预估成本做熔断，不是供应商实际账单。
- WebSocket 代理（HTTP/SSE 已支持）。
- 多实例部署：SQLite 适合单实例；多实例需 PostgreSQL/MySQL 和跨进程锁。
- 公网 HTTPS、FRP 回源、域名与防火墙。
- 未知写路径默认放行；生产验证完 UI 路由后建议把 `DENY_UNKNOWN_WRITES=true`。
