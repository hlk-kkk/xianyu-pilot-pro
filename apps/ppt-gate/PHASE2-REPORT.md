# ppt-gate 阶段 2 实施与验证报告

- 日期：2026-11-01
- 目标：代理层计次、额度结算、预算熔断、闲鱼卡密组导入
- 结论：**计次、结算、预算熔断已实现并通过线上 5001 端到端验证；闲鱼导入已实现并通过桩服务契约测试，但未对真实闲鱼后端做在线导入。**

## 1. 实现范围

| 模块 | 文件 | 职责 |
|---|---|---|
| 动作识别 | `app/metering.py` | 识别生成 / 异步生成 / AI 改稿 / 生图，解析页数与幂等键 |
| 套餐 | `app/plans.py` | 套餐定义、快照、增删改查 |
| 额度与预算 | `app/quotas.py` | 原子预留、结算、释放、并发限制、每日预算熔断 |
| 结算 worker | `app/settlement.py` | 轮询异步任务状态，完成结算、失败释放、超时标记 unknown |
| 配置存储 | `app/gate_settings.py` | 预算、预估成本、并发、开关 |
| 闲鱼导入 | `app/xianyu.py` | 前台用户登录、卡密组查询/创建、批量导入 |
| 代理 | `app/proxy.py` / `app/main.py` | 计次后转发、异步响应缓冲、管理接口 |

## 2. 计次规则

| 动作 | Presenton 路径 | 额度 | 结算方式 |
|---|---|---|---|
| 同步生成 | `/api/v1/ppt/presentation/generate`、`/api/v2/...`、`/create` | generation | 2xx 后结算 |
| 异步生成 | `/api/v1/ppt/presentation/generate/async`、`/api/v2/...` | generation | 取 task id，worker 轮询后结算 |
| AI 改稿 | `/presentation/edit`、`/derive`、`/ppt/chat/*`、`/ppt/theme/generate` | ai_edit | 2xx 后结算 |
| 生图 | `GET /api/v1/ppt/images/generate` | image | 2xx 后结算 |
| 导出/保存/上传 | `/export`、`/update`、`/slide_update`、`/images/upload` 等 | 免费 | 不预留 |

- 计次前检查：兑换码有效期、套餐额度、单份页数、全局/单卡并发生成数、每日预算。
- 预留写入 `usage_reservations`，状态 `reserved`；额度表同步增加 `reserved` 计数。
- 同步 2xx → `settled`；非 2xx → `released`。
- 异步完成 → `settled`；失败 → `released`；超过 30 分钟仍无状态 → `unknown`，保留额度等待管理员对账。
- 计费金额为**预估成本**，用于预算熔断，不代表供应商真实账单。

## 3. 默认套餐

| 套餐 | 生成 | AI 改稿 | 生图 | 最大页数 | 有效期 |
|---|---:|---:|---:|---:|---:|
| trial | 2 | 5 | 0 | 10 | 7 天 |
| basic | 10 | 30 | 0 | 20 | 30 天 |
| pro | 30 | 100 | 20 | 40 | 30 天 |

已有兑换码在激活时写入额度快照，后续修改套餐不影响存量卡。

## 4. 闲鱼卡密组导入

使用前台租户用户 API：

- `POST /api/login/login` 获取用户 JWT
- `POST /api/cards/{groupId}/items/batch` 批量导入卡密内容

卡密内容为兑换链接 `https://<gate>/r/PPT-...`。批次创建接口支持：

- `import_to_xianyu`
- `xianyu_group_id`
- `xianyu_group_name`

未指定组 ID 时按名称查询，不存在则自动创建 `cardType=kami` 的卡密组。导入结果写入 `card_batches.xianyu_*` 字段。

**验证状态**：通过桩服务验证了登录、组查询/创建、批量导入和批次状态写入。真实闲鱼 core-api 当前未在本机运行，因此**未做在线导入**；上线前需要用真实租户用户和卡密组跑一次。

## 5. 单元测试

```bash
cd apps/ppt-gate
.venv/bin/pytest -q
```

结果：**31 passed**。

新增覆盖：

- 动作识别、免费写路径、页数解析、task id/幂等键解析
- 原子预留、结算、释放、额度耗尽
- 每日预算熔断、AI 改稿零额度
- 幂等请求键
- 同步生成扣次与二次拦截
- 异步任务预留与 worker 结算
- 闲鱼登录、组查询/创建、批量导入、批次状态

## 6. 线上 5001 端到端

测试环境：门禁 `127.0.0.1:8009`，Presenton `127.0.0.1:5001`，自定义套餐 `one`（1 次生成、0 次 AI 改稿、最多 5 页）。

| 检查项 | 结果 |
|---|---|
| 同步生成 2 页 | 200，**84.7s** |
| 用量汇总 | `settled: 1`，`generation: 1`，今日成本 20 分 |
| 卡额度 | `generations_total=1`、`used=1`、`reserved=0` |
| 第二次生成 | **403**，提示额度已用 1、上限 1 |
| AI 改稿（额度 0） | **403** |
| 请求 6 页（上限 5） | **400**，未新增预留 |
| 每日预算设为 0 | **429**，未转发、未预留 |
| 异步生成 | 200，任务完成 |
| 异步结算 | worker 自动置为 `settled`，成本 20 分，额度 `used=1` |
| 未配置闲鱼 | `/admin/xianyu/groups` 返回 **503**，符合预期 |

门禁日志无错误；测试服务已停止，临时数据库、Cookie 和含密钥的环境文件已删除。

## 7. 当前限制

1. **成本是预估值**：没有读取供应商真实 token/图片账单；预算熔断基于 `estimated_*_cents`，需要按实际账单校准。
2. **未知写路径默认放行**：`DENY_UNKNOWN_WRITES=false`。完成 UI 路由审计后建议改为 `true`，避免新增的模型调用路径绕过计次。
3. **生图未实测**：线上 Presenton 仍关闭 AI 配图；生图路径已计次但未跑真实生成。
4. **闲鱼在线导入未测**：需要运行 core-api、创建专用租户用户和卡密组后联调。
5. **SQLite 单实例**：多实例部署需要 PostgreSQL/MySQL 与跨进程锁。
6. **WebSocket 未代理**：HTTP/SSE 已支持。
7. **测试残留**：线上 Presenton 留下了阶段 1/2 的 E2E 测试用户，可在确认无用后通过管理员接口清理。
