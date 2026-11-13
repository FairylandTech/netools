# Project instructions

本项目使用 Python 3.13、uv、FastAPI、HTTPX 和 Redis，提供 IP 风险评分（Scamalytics）与 IP 地址信息（ipregistry）查询。

## 先读什么

- 代码结构、分层与不变量：`.claude/docs/architecture.md`
- HTTP 接口与错误代码：`.claude/docs/api.md`
- 测试布局与验证命令：`.claude/docs/testing.md`
- 0.1.x → 0.2.0 的模块与路径映射：`.claude/docs/refactoring-0.2.md`
- 部署、运维、额度语义（历史文档，接口路径以 `.claude/docs/api.md` 为准）：`.codex/docs/`

新写的设计、接口或验证文档放在 `.claude/docs/`，使用中文；代码标识符、错误代码和日志使用英文。

## 代码布局与依赖方向

```text
src/netools/
  core/      共享基础设施：Settings、ServiceError、IP 规范化、AdmissionGate、SingleFlightCache、KeyPool 基类、unlock.lua
  ip_risk/   Scamalytics：config / client / pool / service / models / diagnostics / lua
  ip_info/   ipregistry：config / client / pool / service / models / lua
  lookup/    请求级编排：准入、总超时、批量、风险+信息合并
  api/       鉴权、错误映射、/healthz /readyz、v1 路由（risk / info / lookup / legacy）
  app.py     create_app：生命周期内装配并挂到 app.state
```

- 依赖只能向下：`api → lookup → ip_risk | ip_info → core`。`core` 不导入业务域；`ip_risk` 与 `ip_info` 互不导入；跨域组合只写在 `lookup`。
- 供应商专属对象以供应商命名（`ScamalyticsClient`、`IpregistryKeyPool`），对外能力以能力命名（`RiskService`、`InfoService`、`LookupService`）。
- 新增供应商时按 `.claude/docs/architecture.md` 的"新增一个供应商"步骤建包，不往 `core` 里塞供应商逻辑。
- 新端点放在 `api/v1/<capability>.py`，路径前缀 `/v1/ip/<capability>`，至少提供 `GET ""`、`POST /batch`、`GET /keys`；路由层不写业务判断。

## 接口兼容

- `/v1/score`、`/v1/scores`、`/v1/keys` 是弃用路径，响应结构必须与 0.1.x 一致；不要删除或改变它们的字段，新功能只加到 `/v1/ip/*`。
- 错误代码是接口契约：风险域无前缀，信息域 `ipregistry_` 前缀，基础设施 `overloaded` / `state_unavailable` / `lookup_timeout` / `unauthorized`。改动或新增时同步更新 `.claude/docs/api.md`。
- Redis key 命名空间（见 architecture.md）是部署契约；改动会丢失线上额度账本，不要改。

## 并发、额度与凭证规则

- 所有出站 HTTP 与 Redis 操作必须异步；整个进程共享一个 HTTPX 客户端，不在请求内新建连接池。
- Key 池额度、速率与并发预留通过各域 `lua/reserve.lua` / `finish.lua` 原子执行；不得改成进程内计数。
- 同一账本内余额只减不增；失败、取消、超时不返还预留额度。Scamalytics 按 UTC 月分桶，ipregistry 账本长期有效、不设 TTL。
- Scamalytics 的 username、Key、节点是一组凭证，同一账户不能作为多个独立额度池；ipregistry 以 `account_id` 标识账户，同一账户只配一个 Key。
- `test=1` 仅用于 Scamalytics 额度预检，测试分数不能作为真实分数返回；不要逐 IP 发测试请求。ipregistry 没有预检，首个预算来自 `initial_credits`。
- 合并查询先风险后信息：风险失败直接返回 HTTP 错误并停止，信息失败只写进 `info_error`。
- 不记录上游 URL、请求参数、原始异常或响应错误正文；它们可能包含 API Key。错误消息只含固定枚举和文案。
- `.env`、`config/scamalytics-keys.json`、`config/ipregistry-keys.json` 不入库，不把真实凭证放进镜像或测试。

## 验证

- 依赖：`uv sync --frozen`；依赖变化后更新并提交 `uv.lock`。
- 每次改动后运行：`uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`。
- 集成测试使用独立 Redis（默认 `redis://127.0.0.1:16379/15`，可用 `TEST_REDIS_URL` 覆盖），所有测试资源使用随机前缀；Redis 不可达时测试失败而非跳过。
- 修改并发、额度、重试、租约或 Lua 时，在 `tests/<domain>/test_pool.py` 或 `test_service.py` 补充真实 Redis 的并发/故障测试；`tests/api/` 的 Redis 桩不能替代。
- 测试目录镜像源码包：`tests/core`、`tests/ip_risk`、`tests/ip_info`、`tests/lookup`、`tests/api`；协议桩放 `tests/support.py`。
