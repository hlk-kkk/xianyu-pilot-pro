## Why

PPT 试运营目前把“卖家订单 API 同步”当成自动发货前提，但当前闲鱼账号没有卖家中心权限，`merchant.sold.get` 返回 `PERMISSION_EXCEPTION::无权限访问`；同时扫码会话曾短暂失效、兑换链接在部分内置浏览器中丢失会话 Cookie，导致试运营无法按预期闭环。需要把交付模型收敛为“WS 付款提醒为主、手动发货兜底、订单同步可选”，并明确扫码会话与兑换入口的边界。

## What Changes

- 明确自动发货主路径为 WebSocket `contentType=26`（已付款待发货），不依赖卖家订单 API。
- 卖家订单 API 仅作为补单/兜底；没有卖家中心权限时不阻塞试运营。
- 明确卖家账号会话要求：同一 `externalUid` 只保留一条启用记录；扫码后不得再打开卖家 App；重复账号停用。
- 明确兑换入口行为：`/r/{code}` 本身不需要既有 Cookie，但兑换后必须保存 `ppt_gate_session` 与 `presenton_session` 才能进入 Presenton 工作台。
- 对丢失会话 Cookie 的浏览器提供明确提示，并评估 200 落地页方案，减少重定向闪烁。
- 增加手动发货兜底、试运营验收步骤和账号会话健康检查。
- 不新增绕过平台权限的抓取、伪造请求或直写数据库路径。
- 不改变卡密计次、套餐、预算熔断和 Presenton 代理的既有语义。

## Capabilities

### New Capabilities

- `ppt-auto-delivery`：WS 付款提醒触发的卡密认领、发货记录、消息发送、手动兜底与会话健康要求。
- `ppt-redeem-entry`：兑换码入口、会话 Cookie 交接、浏览器兼容提示与失败诊断。

### Modified Capabilities

无。当前 `openspec/specs/` 为空，本变更只新增 capability。

## Impact

- `apps/automation-service`：`ws_delivery_handler`、`ws_startup`、账号会话/WS 健康检查；主路径不依赖卖家订单 API。
- `apps/ppt-gate`：兑换响应、Cookie 交接、错误提示/落地页、诊断日志。
- 闲鱼后台与用户端：重复账号停用、手动发货、测试订单/商品库存操作。
- 文档与部署：试运营 runbook、权限矩阵、Mac/FRP 可用性说明、回滚步骤。
- 测试：WS `contentType=26`、无订单 API 时发货、Cookie 交接、重复账号会话冲突、手动发货兜底。
