# PPT 试运营 Runbook

本 runbook 对应 OpenSpec change `xianyu-ppt-trial-delivery`。目标是用 WS 付款提醒验证真实自动发货，不依赖卖家中心订单 API。

## 1. 前置检查

- 阿里云 `ppt-gate`、`frps` active；`https://ppt.linjitech.com/health` 200。
- Mac Presenton `127.0.0.1:5001`、core-api `127.0.0.1:18080`、frpc 正常。
- 同一 `externalUid` 只保留一条启用账号；运行：

```bash
XIANYU_ADMIN_USERNAME=admin XIANYU_ADMIN_PASSWORD=... \
  python scripts/check_ppt_trial_accounts.py
```

- 测试商品可售、库存 > 0；卡密组有可用卡密。
- 买家账号可用，且与卖家账号不同。

## 2. 账号扫码

1. 用卖家账号的闲鱼 App 扫码；
2. 扫码确认后**不要打开卖家 App**；
3. 买家操作使用另一台设备；
4. 确认账号 `cookieStatus=1`、`wsStatus=1`。

如果扫码后会话很快失效：

- 检查是否还有重复启用的 `externalUid`；
- 检查是否打开了卖家 App；
- 重新扫码时使用 `accountId=<现有账号ID>`，不要新建重复账号。

## 3. 自动发货配置

- 商品付款后发货：`enabled=1`、`mode=card`、`cardGroupId=<卡密组>`、`cardTemplate={链接}`。
- 确认卡密组内有 `https://ppt.linjitech.com/r/...` 格式的可用卡密。

## 4. 付款测试

1. 买家账号下单并付款；
2. 观察 automation 日志是否出现 `contentType=26` / `检测到待发货消息`；
3. 检查 `card_item` 是否有一张变为已使用；
4. 检查 `delivery_record` 是否新增成功记录；
5. 买家聊天是否收到兑换链接。

卖家订单 API 权限失败不影响本路径；订单同步只作为补单。

## 5. 兑换验证

- 在 Safari/Chrome 打开兑换链接；
- 默认返回 **200 落地页**，设置 `ppt_gate_session` 和 `presenton_session`；
- 页面调用 `/api/v1/auth/verify` 检查 Cookie：
  - 生效：自动进入 Presenton 工作台；
  - 未生效：显示 Safari/Chrome、清除站点数据的提示，不再闪烁。
- 生成 2 页 PPT；检查门禁额度和用量。

需要回滚到旧的 303 跳转时，在 `/opt/ppt-gate/.env` 设置：

```bash
REDEEM_LANDING_ENABLED=false
```

然后重启 `ppt-gate`。

## 6. 手动兜底

自动发货不可用（WS 离线、付款提醒丢失、无卖家中心权限、订单同步失败）时使用。

### 6.1 准备信息

- 订单号 / 买家 UID（闲鱼 App 订单详情）；
- 订单已付款；
- 卡密组有可用卡密；
- 商品发货配置使用 `mode=card`、`cardGroupId=<卡密组>`、`cardTemplate={链接}`；
- 账号 WS 在线（手动发货仍通过 WS 发送消息）。

### 6.2 执行手动发货

优先使用用户端/后台订单页的“手动发货”：

1. 选择卡密组/货源；
2. 确认模板为 `{链接}`；
3. 提交后检查 `delivery_record` 是否新增记录；
4. 检查卡密组中是否有一张卡被认领并标记已使用；
5. 确认买家聊天收到兑换链接。

接口方式（需要登录态）：

```text
POST /api/orders/{orderId}/manual-delivery
{
  "sourceId": <绑定卡密组的货源ID>,
  "deliveryMode": "card",
  "deliveryTiming": "after_payment",
  "quantityRequested": 1
}
```

没有货源时，先在货源库创建卡密货源并绑定卡密组。

### 6.3 去重检查

手动发货会写入 `delivery_record`。后续同一订单的 WS 付款消息应被 `_has_existing_realtime_delivery` 跳过：

- 按 `order_id` 命中已有记录；
- 或按 sid/商品/买家/内容交叉维度命中；
- 或本地订单 `order_status=3` 命中。

如果发现重复发货：

1. 检查 `delivery_record` 是否已有成功记录；
2. 检查卡密是否被重复认领；
3. 不要重复点击手动发货；
4. 保留记录并联系开发排查去重 SQL。

### 6.4 无法发送时

如果账号 WS 离线，手动发货可能在认领后发送失败并回滚卡密：

- 先重新扫码恢复 WS；
- 再用后台手动发货；
- 或直接从卡密组取一条可用链接，手动发给买家，先保证交付。

## 7. 常见问题

| 现象 | 检查 |
|---|---|
| 没有 `contentType=26` | 账号 WS 是否在线、是否打开了卖家 App、是否重复账号 |
| 有付款消息但未认领卡密 | 商品发货配置、卡密组库存、卡密组租户 |
| 兑换 401/闪烁 | 浏览器 Cookie、内置 WebView、站点数据 |
| 兑换后 Presenton 打不开 | 7081 隧道、Presenton 容器、gate 日志 |
| 订单同步失败 | 卖家中心权限；主路径不依赖它 |
| 重复发货 | `delivery_record` 去重窗口、卡密状态 |

## 8. 清理与回滚

- 测试后下架/删除测试商品；
- 撤销测试卡密，标记测试卡密组条目无效；
- 关闭落地页开关恢复 303（若已启用）；
- 停用重复账号，保留单一启用账号；
- 记录测试结果到试运营报告。
