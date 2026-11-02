# ppt-gate 阶段 1 实施与验证报告

- 日期：2026-11-01
- 目标：卡密核销、Presenton 用户映射、免二次登录、反向代理骨架
- 结论：**四项全部实现并通过单元测试与线上 5001 端到端验证**

## 1. 实现范围

新增独立服务 `apps/ppt-gate`，FastAPI + SQLite，不侵入 `automation-service`。

| 模块 | 文件 | 职责 |
|---|---|---|
| 配置 | `app/config.py` | 环境变量、生产 fail-closed 校验 |
| 安全 | `app/security.py` | 兑换码生成/哈希、会话签名、影子凭据加密 |
| 数据库 | `app/db.py` | cards / presenton_users / audit_log / usage_reservations |
| 卡密 | `app/cards.py` | 批次生成、原子核销、激活、重试、封禁 |
| Presenton 客户端 | `app/presenton.py` | 管理员登录、创建用户、用户登录、verify |
| 会话 | `app/sessions.py` | `ppt_gate_session` 与 `presenton_session` Cookie |
| 代理 | `app/proxy.py` | 请求/响应流式转发、Cookie 过滤、SSE 支持 |
| 入口 | `app/main.py` | `/r/{code}`、`/admin/cards*`、反向代理、权限边界 |
| CLI | `app/cli.py` | 批量生成兑换码 |

## 2. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/r/{code}` | 核销并登录，303 跳转到 `/` |
| POST | `/admin/cards/batch` | 生成兑换码批次（`X-Admin-Token`） |
| GET | `/admin/cards` | 查看卡密状态（不返回原文） |
| POST | `/admin/cards/{id}/revoke` | 封禁兑换码 |
| GET | `/health` | 健康检查 |
| 其他 | `/*` | 鉴权后代理到 Presenton |

## 3. 端到端验证（线上 5001）

测试环境：门禁 `127.0.0.1:8009`，Presenton `127.0.0.1:5001`（MiniMax M3 配置）。

| 检查项 | 结果 |
|---|---|
| 生成 1 张兑换码 | 成功，28 字符 `PPT-...` 格式 |
| 首次兑换 `/r/{code}` | **303**，下发 `ppt_gate_session` + `presenton_session` |
| Presenton 影子用户 | 创建成功：`card_d9ba1d86828dce4f`，role=`user` |
| 门禁内 `/api/v1/auth/verify` | 200，返回该影子用户 |
| 门禁内 `/api/v1/ppt/presentation/all` | `[]` |
| 门禁内 `/api/v1/admin/users` | **403** |
| 通过门禁生成 2 页 PPT | 成功，**55.3s**，导出 **196,168 bytes** |
| PPTX 校验 | 合法 ZIP，2 个 slide |
| 通过门禁下载 PPTX | 200，`application/vnd.openxmlformats-officedocument.presentationml.presentation` |
| 同一兑换码再次打开 | **303**，映射到同一用户 ID |
| 仅带门禁 Cookie 请求 `/auth/verify` | 200，并自动补发 `presenton_session` |
| 卡密数据库 | 1 张 `active`，1 条用户映射 |
| 审计 | `batch.created`、`card.activated` |

观察：轮询生成状态时出现过一次瞬时 `ConnectionError`，下一次轮询恢复；门禁日志无错误。后续可在门禁代理层对幂等 GET 增加一次重试。

## 4. 单元测试

```bash
cd apps/ppt-gate
.venv/bin/pytest -q
```

结果：**16 passed**。

覆盖：

- 兑换码格式、归一化、pepper 哈希
- 会话签名/篡改/过期
- 影子凭据加解密
- 原子核销、激活、重试、封禁
- 重复兑换幂等
- 代理只转发 `presenton_session`，不泄露 `ppt_gate_session`
- 管理端、登录、setup、MCP 路径 403
- 无门禁会话 401
- 过期卡：GET 放行、生成 403、导出放行
- Presenton Cookie 缺失时自动续签

## 5. 安全边界

- 兑换码只存 HMAC 哈希，原文只在生成时显示一次。
- 影子用户密码用 `GATE_SECRET` 派生 Fernet 密钥加密存储。
- 买家被禁止访问 `/api/v1/admin/*`、`/mcp`、`/api/v1/auth/login`、`/api/v1/auth/setup`。
- `GATE_SECRET`、`CARD_PEPPER`、`GATE_ADMIN_TOKEN` 生产环境要求 ≥32 字符且 fail-closed。
- 启动命令关闭访问日志，避免兑换码进入 URL 日志。
- 代理不把 `ppt_gate_session` 转发给 Presenton。

## 6. 残留与清理

- 线上 Presenton 保留了一个 E2E 测试用户：`card_d9ba1d86828dce4f`（ID `b2d81219-db80-4d53-b7bb-0735700f7fa3`）。如需清理，用 Presenton 管理员接口删除。
- E2E 生成的样例已保存到 `AI_PPT生成/phase1/sample-2pages-gate.pptx`。
- 临时门禁数据库、测试 Cookie 和含密钥的临时环境文件已删除；门禁进程已停止。

## 7. 阶段 1 未覆盖

- 按操作计次、额度结算与全局预算熔断（`usage_reservations` 已建表）
- WebSocket 代理（HTTP/SSE 已支持）
- 闲鱼卡密组自动导入（当前 CLI/管理接口导出后人工导入）
- 公网 HTTPS、FRP 回源、域名与防火墙
- 多进程/多实例部署（SQLite 当前适合单实例）
- Presenton `/api/v1/ppt/presentation/status/{id}` 的 500 bug：统一使用 `/api/v1/async-tasks/status/{id}`
