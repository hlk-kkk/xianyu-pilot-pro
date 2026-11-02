## Context

PPT 试运营链路已经具备：阿里云 Nginx + `ppt-gate`、FRP 回源 Mac Presenton、闲鱼卡密组导入、兑换与计次。当前暴露出的试运营问题集中在三处：

1. 卖家账号没有卖家中心权限，`merchant.sold.get` 返回 `PERMISSION_EXCEPTION::无权限访问`；
2. 卖家账号与 tenant 4 存在同一 `externalUid=2980835701` 的重复记录，扫码会话曾在约 1 分钟后失效，WS 断开；
3. 兑换链接 `/r/{code}` 服务端返回 303 并下发会话 Cookie，但闲鱼内置 WebView/部分浏览器未保存 Cookie，导致 `/` 返回 401 或页面闪烁。

代码事实：`ws_delivery_handler` 的主路径是 WebSocket `contentType=26`（已付款待发货），不依赖卖家订单 API；订单同步只是补单/兜底。卡密认领、发货记录、消息发送均已存在。

约束：

- 不绕过闲鱼平台权限，不伪造请求、不直写闲鱼数据库；
- Presenton 原生工作台依赖会话 Cookie，无法做到完全无 Cookie；
- 试运营仍运行在 Mac + FRP 单点上；
- 生产账号可继续用于受控测试，但会话必须独占。

## Goals / Non-Goals

**Goals:**

- 让自动发货以 WS 付款提醒为主路径工作，不要求卖家中心权限。
- 明确卖家账号会话独占、重复账号停用和重扫码 runbook。
- 让兑换入口在正常浏览器与内置浏览器下都有明确行为，不出现无解释的闪烁/401。
- 保留手动发货兜底，并在无卖家中心权限时仍可完成试运营验收。
- 记录可诊断的非敏感上下文，便于定位会话、Cookie、卡密和发货问题。

**Non-Goals:**

- 不实现绕过卖家中心权限的订单抓取。
- 不重写 Presenton 原生编辑器，也不把工作台改成无 Cookie 架构。
- 不在本变更中迁移 core-api 到独立服务器。
- 不改变套餐、计次、预算熔断和卡密加密语义。
- 不保证生产账号长期无人值守；独立卖家账号仍建议后续评估。

## Decisions

### 1. WS-first delivery, seller API optional

自动发货以 `contentType=26` 付款提醒为主路径；卖家订单 API 只作为补单/对账的可选能力。没有卖家中心权限时，系统仍通过 WS 和手动发货工作。

替代方案：依赖订单 API 轮询。被否决，因为当前账号无权限，且会延迟发货。

### 2. One enabled account per externalUid

同一 `externalUid` 在本地只保留一条启用账号。试运营期间停用 tenant 4 的重复记录，tenant 1 只保留一个账号；扫码后不再打开卖家 App，买家操作使用另一台设备。

替代方案：两个租户同时在线。被否决，因为会话刷新会互相顶掉，导致 WS 频繁断开。

### 3. Redeem landing page instead of bare redirect

保留 `/r/{code}` “只需兑换码”的入口语义：首次请求不要求既有 Cookie；成功后仍设置 `ppt_gate_session` 和 `presenton_session`。把当前 303 跳转改为一个可配置的 **200 落地页**：

- 页面设置会话 Cookie；
- 通过 `fetch('/api/v1/auth/verify', {credentials:'include'})` 检查 Cookie 是否生效；
- 生效则自动/手动进入 `/`；
- 未生效则显示“请用 Safari/Chrome 打开、清除站点数据”，不再无限闪烁。

替代方案 A：保留 303，只改 401 页面。改动小，但内置 WebView 仍可能闪烁，诊断信息不足。
替代方案 B：用 URL token 承载会话。被否决，因为 Presenton UI 与 API 仍需要 Cookie，且会扩大 token 泄露面。

### 4. Manual delivery is a first-class fallback

无卖家中心权限、WS 断开或付款提醒丢失时，运营可用现有手动发货接口/页面认领卡密并发送链接。手动发货不走订单同步，不伪造订单，不绕过平台权限。

### 5. Observability without secrets

在门禁和 WS 侧记录：

- 兑换：卡密前缀、卡状态、Presenton 登录结果、Cookie 是否下发；
- WS：账号 ID、`contentType=26` 是否收到、是否命中规则、是否认领卡密、是否发送成功；
- 会话：账号 enabled、WS 状态、最近心跳/失效时间。

禁止记录完整兑换码、Cookie、Presenton 密码和买家隐私内容。

## Trial Results (2026-11-03)

- **WS-first delivery verified**：买家付款后收到 `contentType=26`，系统匹配商品级卡密配置、认领卡密并通过 WS 发送兑换链接；买家确认收到。
- **Order API remains unavailable**：`merchant.sold.get` 仍返回 `PERMISSION_EXCEPTION::无权限访问`，但不影响 WS 主路径；本地 `xianyu_trade_order` 为空，订单号无法反查，平台确认发货会被跳过。
- **QR login**：passport 扫码只能拿到 12 个 Cookie，缺 `_hvn_lgc_` / `havana_lgc_exp`，WS 约 1 分钟失效。已切换为 crawler-service 的 Playwright 浏览器扫码，能拿到 18 个 Cookie，但当前账号的会话仍约 30-60 分钟。
- **Manual long-lived cookie verified**：使用桌面 Chrome 勾选“保持登录”后导出的完整 Cookie（含 `_hvn_lgc_`、`havana_lgc_exp`、`m_h5_tk`）写入账号 2，`havana_lgc_exp=2026-12-03`（约 30 天），`authUsable=true`，WS 连接成功，1 分钟后 `Cookie 保活成功`。生产应优先使用这种方式，而不是每次扫码。
- **Schema drift found**：本地库缺 `delivery_record.receiver_info`、`delivery_record.delivery_time` 两列，以及 `xianyu_captcha_backoff`、`xianyu_account_solve_exclusion` 两张表。已补列/建表，避免实时发货写入失败和 WS 健康检查每 2 分钟报错。
- **Delivery record corrected**：卡密发送成功现在写 `status=2`、`delivery_status=success`，并回填 `card_item_id`、`quantity_requested`、`quantity_sent`、`delivery_time`、`completed_time`；无 order_id 不再误标 pending。
- **Redemption + Presenton verified**：使用独立测试卡走真实 `/r/{code}`，200 落地页、`ppt_gate_session`/`presenton_session` 下发、`/api/v1/auth/verify` 通过；生成 3 份 2 页 PPT，导出 PPTX 均为 2 页且 ZIP 有效。
- **Quota and budget verified**：3 次同步 generation 全部 settled，额度 3/3、预估成本 60 分；第 4 次被门禁 403 拦截；日预算 10000 分，剩余 9940 分。
- **Performance（n=3，P95 用样本最大值近似）**：兑换 P50 0.353s / P95 0.553s；2 页生成 P50 47.727s / P95 67.927s；PPTX 导出 P50 7.948s / P95 8.314s；导出文件 195–256KB。
- **Still open**：阶段 3 报告、Mac/FRP 监控与告警、长期登录态方案；`deny_unknown_writes=false` 仍允许未识别写路径绕过计次。

## Risks / Trade-offs

- **WS 会话被手机端顶掉** → 账号独占、停用重复记录、扫码后不打开卖家 App；增加 WS 健康检查与重扫码 runbook。
- **Mac 休眠/FRP 断线** → 生产不可用；保持 Mac 唤醒、Docker 与 frpc 自启，增加隧道健康告警；长期考虑独立服务器。
- **落地页仍无法修复 Cookie 被完全禁用** → 页面明确提示改用 Safari/Chrome，不承诺无 Cookie 可用。
- **没有卖家订单 API** → 无法自动补单/对账；依赖 WS + 手动发货，运营需接受这一限制。
- **落地页改变现有 303 行为** → 需要回归兑换、重复兑换、分享链接、内置浏览器；提供配置开关以便回滚。
- **生产账号用于测试** → 可能接触真实消息/订单；试运营应使用独立测试账号，或严格控制测试商品和买家账号。

## Migration Plan

1. 停用 tenant 4 重复账号，只保留 tenant 1 账号；确认同一 `externalUid` 只有一条 enabled。
2. 部署门禁落地页（可配置开关），保留旧 303 行为作为回滚路径。
3. 补 WS `contentType=26`、无订单 API、手动发货、Cookie 交接、重复账号会话的测试。
4. 试运营前准备：卖家账号 WS 在线、测试商品可售、买家账号可用、兑换在 Safari/Chrome 验证。
5. 回滚：关闭落地页开关恢复 303；停用新逻辑不影响既有卡密、订单和计次数据。

## Open Questions

- 试运营是否继续使用生产账号，还是尽快换独立测试卖家账号？
- 是否有稳定可用的买家账号用于多轮付款测试？
- 落地页自动跳转的等待时间与文案是否需要按闲鱼内置浏览器单独调整？
- 是否申请卖家中心/鱼小铺权限以启用订单同步兜底？
- Mac + FRP 的单点风险是否需要在试运营前迁到独立服务器？
