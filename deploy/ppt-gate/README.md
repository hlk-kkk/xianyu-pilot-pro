# 公网 HTTPS / FRP 部署说明（阶段 3）

> 2026-11-01 实际部署：阿里云已有 frps 0.71.1，仅把 `allowPorts` 从 7080 改为 7081；证书、Nginx 站点和 `/opt/ppt-gate` 门禁已部署。当前生产门禁 `XIANYU_IMPORT_ENABLED=false`，待有阿里云可达的 core-api 和专用租户账号后再开启。

## 目标拓扑

```text
买家浏览器
  │ https://ppt.linjitech.com:443
  ▼
阿里云 Nginx（TLS）
  │ http://127.0.0.1:8009
  ▼
Aliyun ppt-gate
  │ http://127.0.0.1:7081（FRP 回源）
  ▼
Mac frpc ──► 127.0.0.1:5001 Presenton
```

- `7081` 只允许绑定在阿里云 `127.0.0.1`，公网不可达。
- `7080` 不用于 Presenton；上线后必须确认公网 7080/7081 均不可达。
- `7000` 是 FRP 控制端口，仅对 Mac 的公网 IP 开放，并启用 token + TLS。

## 当前状态（2026-11-01）

| 项目 | 状态 |
|---|---|
| `ppt.linjitech.com` DNS | 已解析到 `8.148.80.73` |
| 阿里云 80/443 | 开放，现有 `ai.linjitech.com` 正常 |
| 阿里云 7000 | **公网超时，未开放** |
| 阿里云 7080 | 公网超时（当前不可达） |
| SSH | `root` / `ubuntu` 均被拒绝，**需要补充访问方式** |
| frps | **未安装/未运行** |
| Mac frpc | **未运行；`~/presenton_data/frp/frpc.toml` 缺失** |
| 门禁公网部署 | **未执行** |

## 前置条件

1. 阿里云 SSH：提供可用的用户名与密钥，或把公钥加入 `~/.ssh/authorized_keys`。
2. 阿里云安全组：放行 TCP 7000（建议只允许 Mac 当前公网 IP），不要放行 7080/7081。
3. `certbot` 可用（或手动放置 `ppt.linjitech.com` 证书）。
4. 一个正式的闲鱼租户服务账号（不要使用 `admin/123456`）。

## 1. 阿里云安装 frps

```bash
# 以 root 执行
cd /tmp
curl -fLO https://github.com/fatedier/frp/releases/download/v0.71.1/frp_0.71.1_linux_amd64.tar.gz
tar -xzf frp_0.71.1_linux_amd64.tar.gz
install -m 0755 frp_0.71.1_linux_amd64/frps /usr/local/bin/frps
install -d -m 0700 /etc/frp
install -m 0600 /path/to/frps.toml.example /etc/frp/frps.toml
# 编辑 /etc/frp/frps.toml，替换 auth.token
```

创建 `/etc/systemd/system/frps.service`：

```ini
[Unit]
Description=frp server
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/frps -c /etc/frp/frps.toml
Restart=always
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now frps
systemctl status frps --no-pager
ss -lntp | grep 7000
```

## 2. Mac 启动 frpc

把工作区的 `frpc` 二进制和 `frpc.toml.example` 放到 `/Users/hulk/presenton_data/frp/`，替换 token，然后：

```bash
cd /Users/hulk/presenton_data/frp
./frpc -c frpc.toml
```

长期运行建议配置 LaunchAgent（或现有进程管理器），确保 Mac 重启后自动拉起。启动后到阿里云验证：

```bash
ss -lntp | grep 7081   # 应为 127.0.0.1:7081，不是 0.0.0.0
```

## 3. 签发证书

DNS 已经指向阿里云，80 端口开放：

```bash
apt-get update
apt-get install -y certbot python3-certbot-nginx
certbot certonly --nginx -d ppt.linjitech.com
```

证书路径应为 `/etc/letsencrypt/live/ppt.linjitech.com/fullchain.pem`。

## 4. 配置 Nginx

把 `nginx-ppt.conf.example` 放到 `/etc/nginx/conf.d/ppt.linjitech.com.conf`，然后：

```bash
nginx -t
systemctl reload nginx
```

不要覆盖或修改现有 `ai.linjitech.com` 的 server block。

## 5. 部署门禁到阿里云

```bash
useradd --system --shell /usr/sbin/nologin --home /opt/ppt-gate pptgate
install -d -o pptgate -g pptgate /opt/ppt-gate
# 从仓库复制 apps/ppt-gate，排除 .venv/data/.env/__pycache__
rsync -a --exclude '.venv' --exclude 'data' --exclude '.env' --exclude '__pycache__' \
  apps/ppt-gate/ /opt/ppt-gate/
python3 -m venv /opt/ppt-gate/.venv
/opt/ppt-gate/.venv/bin/pip install -r /opt/ppt-gate/requirements.txt
```

创建 `/opt/ppt-gate/.env`（权限 600，属主 `pptgate`）：

```bash
APP_ENV=production
DATABASE_PATH=/opt/ppt-gate/data/ppt_gate.db
PUBLIC_BASE_URL=https://ppt.linjitech.com

PRESENTON_BASE_URL=http://127.0.0.1:7081
PRESENTON_ADMIN_USERNAME=<presenton-admin>
PRESENTON_ADMIN_SECRET=<presenton-admin-secret>

GATE_SECRET=<random-32+>
CARD_PEPPER=<random-32+>
GATE_ADMIN_TOKEN=<random-32+>

COOKIE_SECURE=true
SESSION_TTL_SECONDS=2592000
METERING_ENABLED=true
DEFAULT_DAILY_BUDGET_CENTS=10000

XIANYU_IMPORT_ENABLED=true
XIANYU_API_BASE_URL=http://<core-api-host>:18080
XIANYU_USERNAME=<dedicated-tenant-user>
XIANYU_SECRET=<dedicated-user-secret>
# XIANYU_CARD_GROUP_ID=12
XIANYU_CARD_GROUP_NAME=AI PPT 兑换码
```

安装 systemd：

```bash
install -m 0644 ppt-gate.service /etc/systemd/system/ppt-gate.service
systemctl daemon-reload
systemctl enable --now ppt-gate
systemctl status ppt-gate --no-pager
curl -s http://127.0.0.1:8009/health
```

## 6. 上线验收

```bash
# 公网 HTTPS
curl -sS -I https://ppt.linjitech.com/health
curl -sS https://ppt.linjitech.com/health

# 回源端口只在 loopback
ss -lntp | grep -E '7081|8009'

# 公网不能访问 7080/7081
nc -vz -G 3 8.148.80.73 7080
nc -vz -G 3 8.148.80.73 7081
# 预期：超时或 connection refused

# 管理接口
curl -sS -H "X-Admin-Token: <GATE_ADMIN_TOKEN>" https://ppt.linjitech.com/admin/usage/summary
```

再跑一次：

1. 创建 1 张兑换码并导入闲鱼卡密组；
2. 用导入的链接兑换，确认 303 与 Presenton 工作台可用；
3. 生成一次 2 页 PPT，确认用量结算与额度扣减；
4. 把每日预算调低，确认 429 熔断；
5. 确认公网无法直接访问 Presenton 或 7080/7081。

## 7. 当前阻塞

- SSH：`root` / `ubuntu` 使用现有本地密钥均被拒绝，无法安装 frps、配置 Nginx 或部署门禁。
- 安全组：TCP 7000 未开放，frpc 无法连接。
- frps/frpc：均未运行；Mac 侧 `frpc.toml` 缺失（工作区有备份，需要恢复并改 `remotePort=7081`）。

## 8. 备注

- 当前本地联调用的是 `admin/123456` 前台账号（tenant 1），**不能用于生产**。
- 本地旧数据库缺少 `security_version` 默认值，通过旧端点创建新用户会返回 503；生产应使用新库/迁移并创建专用租户用户。
- 本地测试已创建 tenant 1 的卡密组 `id=2`，包含 2 条测试兑换链接；上线前应清理或改用新组。
