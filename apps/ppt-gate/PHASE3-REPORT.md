# ppt-gate 阶段 3 完成报告

- 日期：2026-11-01
- 目标：公网 HTTPS / FRP、验证 7080 不可达、生产闲鱼卡密组自动导入
- 结论：**公网 HTTPS、FRP、门禁部署、生产闲鱼自动导入均已打通并验收。**

## 1. 部署清单

### 阿里云 8.148.80.73

| 组件 | 位置 | 状态 |
|---|---|---|
| frps 0.71.1 | `/usr/local/bin/frps`、`/etc/frp/frps.toml`、systemd `frps` | active / enabled |
| Nginx 站点 | `/etc/nginx/conf.d/ppt.linjitech.com.conf` | active |
| Let's Encrypt 证书 | `/etc/letsencrypt/live/ppt.linjitech.com/` | 至 2027-01-30，自动续期 |
| ppt-gate | `/opt/ppt-gate`、systemd `ppt-gate`、用户 `pptgate` | active / enabled |
| 门禁 SQLite | `/opt/ppt-gate/data/ppt_gate.db` | 自动创建 |
| 门禁配置 | `/opt/ppt-gate/.env`（600，属主 pptgate） | 已启用闲鱼导入 |

frps 关键配置：

- `bindPort = 7000`
- `proxyBindAddr = "127.0.0.1"`
- `allowPorts = [{ start = 7081, end = 7082 }]`
- `transport.tls.force = true`

### Mac

| 组件 | 位置 | 状态 |
|---|---|---|
| Presenton | `127.0.0.1:5001` | running |
| 闲鱼 core-api | `127.0.0.1:18080`（MySQL/Redis/backend 容器） | healthy |
| frpc | `/Users/hulk/presenton_data/frp/frpc` + `frpc.toml` | 由 LaunchAgent `ai.linjitech.presenton-frpc` 管理 |
| 隧道 1 | `5001 → 7081` | Presenton |
| 隧道 2 | `18080 → 7082` | 闲鱼 core-api |

容器重启策略为 `unless-stopped`；Docker 启动后会自动拉起 MySQL/Redis/backend。

## 2. 验收结果

| 检查项 | 结果 |
|---|---|
| `https://ppt.linjitech.com/health` | **200** |
| 证书 | `CN=ppt.linjitech.com`，Let's Encrypt，TLS 校验通过 |
| `https://ai.linjitech.com` | 200，未受影响 |
| `https://tutor.linjitech.com` | 200，未受影响 |
| 公网 80 / 443 / 7000 | 可连接 |
| 公网 7080 | **Connection refused** |
| 公网 7081 | **超时/不可达** |
| 公网 7082 | **超时/不可达** |
| 服务器监听 | `127.0.0.1:7081`、`127.0.0.1:7082`、`127.0.0.1:8009`、`*:7000` |
| frps / ppt-gate | active 且 enabled |
| 隧道 | Presenton 7081 = 200；core-api 7082 = 200 |
| 本地测试 | **31 passed** |

公网端到端：

| 步骤 | 结果 |
|---|---|
| 创建兑换码 | 200，链接为 `https://ppt.linjitech.com/r/...` |
| 兑换 | **303**，Cookie `HttpOnly + Secure` |
| `/api/v1/auth/verify` | 200，返回 Presenton 影子用户 |
| 管理端路径 | 403 |
| 生成 2 页 PPT | 200，**48.5s** |
| 用量结算 | `settled: 1`，预估成本 20 分 |
| 额度 | trial：`used=1 / total=2` |

## 3. 生产闲鱼自动导入

### 专用租户账号

- 用户名：`pptgate_service`
- 租户：tenant 1
- 用户 ID：3
- 权限：可访问 tenant 1 的卡密组 `id=2`（`AI PPT 兑换码`）
- 凭据文件：`/Users/hulk/presenton_data/xianyu-ppt-service.json`（600）

本地旧库 `sys_user.security_version` 没有默认值，后台建用户接口会返回 503；因此该账号通过 SQL 直接插入并写入 `security_version=1`，密码为 bcrypt 哈希。没有修改表结构。

### 门禁配置

生产门禁 `.env` 已启用：

```text
XIANYU_IMPORT_ENABLED=true
XIANYU_API_BASE_URL=http://127.0.0.1:7082
XIANYU_USERNAME=pptgate_service
XIANYU_SECRET=<专用账号密码>
XIANYU_CARD_GROUP_ID=2
XIANYU_CARD_GROUP_NAME="AI PPT 兑换码"
```

### 导入验收

| 检查项 | 结果 |
|---|---|
| 门禁创建批次并导入 | 200 |
| 闲鱼返回 | `status=success`、`group_id=2`、`successCount=1`、`failCount=0` |
| 数据库卡密内容 | `https://ppt.linjitech.com/r/PPT-...` |
| 导入链接兑换 | **303** |
| `/auth/verify` | 200，返回 Presenton 影子用户 |

验收卡已撤销（`revoked`）；闲鱼卡密组中保留了一条已导入的测试链接，可手工删除或标记无效。

## 4. 安全与运行注意事项

- 部署公钥已写入 `/root/.ssh/authorized_keys`，root 密钥登录已验证；服务器没有 `ubuntu` 用户。
- root 密码仍有效，**建议立即轮换并改为仅密钥登录**。
- 本地 core-api 仍是开发实例：
  - 使用旧数据库，用户创建接口存在 schema 问题；
  - 仍有默认 `admin/123456`；
  - 通过 Mac + FRP 暴露到阿里云 loopback 7082，不对公网开放。
- 生产门禁使用专用账号 `pptgate_service` 导入，不用默认管理员。
- 门禁管理 token、Presenton 管理员凭据、闲鱼服务账号密码均在 600 权限文件中，不要提交或外发。
- Mac 必须保持唤醒、Docker 运行，否则 7081/7082 隧道会断开；建议禁用自动睡眠并配置断线告警。
- 服务器仅 1.6GB 内存，当前 frps + Nginx + 门禁可用，但需持续观察。

## 5. 备份与回滚

- Nginx：`/root/nginx-backup-*.tgz`、`/etc/nginx/conf.d/ppt.linjitech.com.conf.bak-*`
- frps：`/etc/frp/frps.toml.bak-*`
- 回滚 Nginx：恢复备份并 `nginx -t && systemctl reload nginx`
- 回滚 frps：恢复备份并 `systemctl restart frps`
- 回滚门禁：`systemctl disable --now ppt-gate`
- 回滚闲鱼导入：把 `/opt/ppt-gate/.env` 的 `XIANYU_IMPORT_ENABLED` 改为 `false` 并重启 `ppt-gate`

## 6. 待办与风险

1. 轮换 root 密码，推荐仅密钥登录。
2. 将闲鱼 core-api 迁到独立服务器或修复旧 schema/迁移，去掉默认 `admin/123456`，再作为真正生产后端。
3. 校准模型真实账单；当前计费用预估成本。
4. 完成 UI 路由审计后把 `DENY_UNKNOWN_WRITES=true`。
5. 清理闲鱼测试组 `id=2` 中的旧测试链接和 Presenton 测试用户。
6. 监控 Mac 睡眠、Docker 重启与隧道断线。
