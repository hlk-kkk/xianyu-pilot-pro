## 1. 账号与会话前置条件

- [x] 1.1 审计 `xianyu_account`，确认同一 `externalUid` 只保留一条启用记录；停用 tenant 4 的重复账号
- [x] 1.2 在门禁/后台增加账号健康检查：`cookieStatus`、`wsStatus`、最近心跳/失效时间
- [x] 1.3 增加启动时重复 `externalUid` 告警，避免多租户同时刷新同一账号
- [x] 1.4 编写重扫码 runbook：使用 `accountId=2` 更新现有账号，扫码后不打开卖家 App
- [x] 1.5 验证扫码后 WS 在线，并记录账号 ID、externalUid、WS 状态

## 2. WS-first 自动发货

- [x] 2.1 核对 `ws_delivery_handler` 对 `contentType=26` 的付款语义识别（“已付款/等待你发货/待发货”）
- [x] 2.2 增加单元测试：收到 `contentType=26` 后认领一张卡、创建 `delivery_record`、发送模板
- [x] 2.3 增加测试：卖家订单 API 不可用时，WS 付款消息仍能完成发货
- [x] 2.4 增加重复付款消息的幂等测试，确认 72 小时窗口内不重复认领/发送
- [x] 2.5 增加可观测日志：type=26 是否收到、规则是否命中、卡密是否认领、消息是否发送
- [x] 2.6 验证手动发货后，后续重复 WS 付款消息不会再次发货
- [x] 2.7 确认主路径不依赖 `merchant.sold.get`，不新增绕过权限的请求

## 3. 兑换入口落地页

- [x] 3.1 增加配置开关 `REDEEM_LANDING_ENABLED`，默认启用，可回滚到 303 行为
- [x] 3.2 实现 `/r/{code}` 成功后的 200 落地页：设置两个会话 Cookie，显示“进入工作台”
- [x] 3.3 落地页通过 `/api/v1/auth/verify` 检查 Cookie 是否生效，生效后进入 `/`
- [x] 3.4 Cookie 未生效时显示明确提示：改用 Safari/Chrome、清除站点数据，不再闪烁
- [x] 3.5 保留重复兑换幂等：同一 active 卡再次打开仍登录同一个 Presenton 用户
- [x] 3.6 增加测试：无 Cookie 首次兑换、Cookie 保留、Cookie 丢失、重复兑换、无效/撤销/过期卡
- [x] 3.7 增加非敏感诊断：卡密前缀、卡状态、Presenton 登录结果、Cookie 是否下发

## 4. 手动发货兜底

- [x] 4.1 验证手动发货接口/页面可从卡密组 2 认领卡密并使用 `{链接}` 模板（命令链路测试通过；KamiDeliveryTest 既有 Mockito 漂移待修）
- [x] 4.2 增加手动发货测试：订单已付款但 WS/订单同步不可用时仍可认领并发送
- [x] 4.3 编写运营 runbook：订单号/买家 UID、发货记录检查、重复发货排查
- [x] 4.4 验证手动发货与后续 WS 自动发货之间的去重

## 5. 试运营验证

- [x] 5.1 在闲鱼 App 补库存/重新上架测试商品，确认 `status` 可售、库存大于 0（2026-11-03：goodsId=8 / 1097153725944，0.01 元，库存 1）
- [x] 5.2 重新扫码更新账号 2，确认 `cookieStatus=1`、`wsStatus=1`，扫码后不打开卖家 App（已改为 crawler Playwright 浏览器扫码）
- [x] 5.3 用独立买家账号下单付款，观察 WS `contentType=26`（买家 2804255088，goodsId=8）
- [x] 5.4 检查 `delivery_record`、卡密组认领结果、买家收到的兑换链接（卡 6 已认领，买家确认收到）
- [x] 5.5 在 Safari/Chrome 打开兑换链接，完成 Presenton 登录与 2 页 PPT 生成（测试卡 batch_8fd0f9775405；生成 3 次、PPTX 2 页、下载 195–256KB）
- [x] 5.6 检查门禁额度、用量结算和预算（3 次 generation 全部 settled，成本 60 分，额度 3/3，第 4 次 403；预算剩余 9940 分）
- [x] 5.7 验证订单 API 权限失败不会阻塞 WS 自动发货（merchant.sold.get 仍返回 PERMISSION_EXCEPTION，WS 发货成功）
- [x] 5.8 记录试运营结果、失败点、P50/P95 耗时和后续修正项（n=3，生成 P50 47.7s / P95 67.9s，导出 P50 7.9s / P95 8.3s）

## 6. 文档、监控与发布

- [ ] 6.1 更新阶段 3/试运营报告：WS-first、卖家权限矩阵、兑换 Cookie 行为
- [ ] 6.2 更新部署说明：落地页开关、手动发货兜底、回滚步骤
- [ ] 6.3 增加 Mac 唤醒、Docker、frpc、7081/7082 隧道的健康监控与告警
- [ ] 6.4 记录安全建议：轮换 root 密码、使用独立卖家账号、禁止绕过平台权限
- [ ] 6.5 验证回滚：关闭落地页开关恢复 303；停用新逻辑不影响既有卡密/订单/计次
- [ ] 6.6 运行完整测试与 `openspec validate`，通过后归档本次 change


## 试运营记录（2026-11-03）

- 测试商品：内部 `goodsId=8`，闲鱼商品 `1097153725944`，0.01 元，卡密组 2，模板 `{链接}`。
- 买家付款后 WS 收到 `contentType=26`，自动匹配商品级配置，认领卡密 6 并通过 WS 发送兑换链接；买家确认收到。
- `merchant.sold.get` 仍返回 `PERMISSION_EXCEPTION::无权限访问`，但没有阻塞 WS 自动发货；本次也通过内部消息重放接口补发了历史付款消息。
- 本次发现并修复：
  - `delivery_record` 缺 `receiver_info`、`delivery_time` 列；
  - 缺 `xianyu_captcha_backoff`、`xianyu_account_solve_exclusion` 两张表；
  - 卡密发货记录未写 `card_item_id` / `quantity_sent`，且无 order_id 时误标 pending；已改为发送成功即 status=2，并回填卡号与数量。
- 扫码会话：passport 接口只返回 12 个 Cookie，缺 `_hvn_lgc_`/`havana_lgc_exp`；已切到 crawler Playwright 浏览器扫码（18 个 Cookie），但长期保持在当前账号上仍约 30-60 分钟，生产建议手动 Cookie 或继续改造登录态。
- 手动完整 Cookie 已验证：浏览器勾选“保持登录”后导出，含 `_hvn_lgc_` / `havana_lgc_exp` / `m_h5_tk`；写入账号 2 后 `authUsable=true`、WS 在线、保活成功，`havana_lgc_exp` 约 30 天后到期。生产优先使用此方式。
- 未完成：买家打开兑换链接后的 Presenton 登录/PPT 生成验证、门禁额度与预算检查、P50/P95 耗时记录、阶段 3 报告与监控文档。

### 性能与额度（2026-11-03，n=3）

| 指标 | P50 | P95（样本最大值） | 范围 |
|---|---:|---:|---|
| 兑换页 GET | 0.353s | 0.553s | 0.322–0.553s |
| 2 页同步生成 | 47.727s | 67.927s | 39.169–67.927s |
| PPTX 导出 | 7.948s | 8.314s | 7.399–8.314s |
| PPTX 大小 | — | — | 195–256KB |

- 门禁额度：3 次 generation 全部 `settled`，`generations_used=3/3`；第 4 次返回 403“当前已用 3、处理中 0、上限 3”。
- 预算：`daily_budget_cents=10000`，3 次预估成本共 60 分，`remaining_budget_cents=9940`。
- 导出文件校验：ZIP 有效，`ppt/slides/slide*.xml` 共 2 页。
- 说明：n=3 的 P95 用样本最大值近似，不能代表生产长尾；上线前建议积累更多样本。
- 已知风险：`deny_unknown_writes=false`，新增未识别的写路径仍可能绕过计次，UI 路由审计后建议改为 true。
