# 0.2.0 重构说明：扁平模块 → 能力域分包

本次重构把 `src/netools/*.py` 的扁平布局改为按能力域分包，并重新设计了 API 路径。行为、Redis 数据格式和错误代码保持与 0.1.x 兼容。

## 模块映射

| 0.1.x | 0.2.0 |
| --- | --- |
| `netools.config.Settings` | `netools.core.config.Settings` |
| `netools.config.KeyConfig` / `Settings.load_keys()` | `netools.ip_risk.config.ScamalyticsKeyConfig` / `load_scamalytics_keys(path)` |
| `netools.config.IpregistryKeyConfig` / `Settings.load_ipregistry_keys()` | `netools.ip_info.config.IpregistryKeyConfig` / `load_ipregistry_keys(path)` |
| `netools.models.ServiceError`、`ErrorDetail` | `netools.core.errors` |
| `netools.models.normalize_ip`、`IPAddress` | `netools.core.ip` |
| `netools.models.Score`、`Risk` | `netools.ip_risk.models.RiskScore`、`Risk` |
| `netools.models.IpregistryData`、`IpregistryLookup` | `netools.ip_info.models.IpInfoData`、`IpInfo` |
| `netools.models.BatchRequest`、`BatchItem`、`BatchResponse` | `netools.lookup.models`（`BatchItem[T]`、`BatchResponse[T]` 泛型） |
| `netools.upstream.ScamalyticsClient`、`UpstreamError`、`Credits`… | `netools.ip_risk.client.ScamalyticsClient`、`ScamalyticsError`、`Credits`… |
| `netools.upstream.retry_after` | `netools.core.http.retry_after` |
| `netools.pool.KeyPool`、`Lease` | `netools.ip_risk.pool.ScamalyticsKeyPool`、`Lease` |
| `netools.service.ScoreService` | 拆为 `netools.ip_risk.service.RiskService`（单 IP）+ `netools.lookup.service.LookupService`（准入、超时、批量、合并） |
| `netools.ipregistry.IpregistryClient`、`IpregistryError` | `netools.ip_info.client` |
| `netools.ipregistry_pool.IpregistryKeyPool` | `netools.ip_info.pool.IpregistryKeyPool` |
| `netools.ipregistry_service.IpregistryService` | `netools.ip_info.service.InfoService` |
| `netools.diagnostics` | `netools.ip_risk.diagnostics`（命令改为 `python -m netools.ip_risk.diagnostics`） |
| `netools/lua/reserve.lua`、`finish.lua` | `netools/ip_risk/lua/` |
| `netools/lua/ipregistry_reserve.lua`、`ipregistry_finish.lua` | `netools/ip_info/lua/reserve.lua`、`finish.lua` |
| `netools/lua/unlock.lua` | `netools/core/lua/unlock.lua` |
| `app.state.pool` / `ipregistry_pool` / `service` | `app.state.risk_pool` / `info_pool` / `lookup` |

Lua 脚本内容未改动。

## 新抽象

- `core.cache.SingleFlightCache[T]`：原来 `ScoreService._lookup` 和 `IpregistryService.lookup` 各自实现的"查缓存 → SET NX 锁 → 再查缓存 → 拉取 → 写缓存 → 比较 token 解锁"合并为一处。Key 前缀由调用方传入，因此 Redis key 不变。
- `core.pool.KeyPool[K, L]`：原来两份 `acquire` 轮询循环合并为基类；Scamalytics 通过 `_recover` 钩子处理 `needs_probe`，并把 `pending_statuses` 扩展为 `{busy, needs_probe}`。
- `core.admission.AdmissionGate`：原来 `ScoreService._active` 计数器独立成对象，由 `LookupService` 持有；所有查询端点共享同一个门。
- `lookup.service.LookupService`：原来 `ScoreService.lookup` 中"准入 + 总超时 + Redis 错误映射 + 风险后拼接 ipregistry"的部分，现在对 `risk()`、`info()`、`lookup()` 三种操作统一处理；`batch(ips, operation)` 对任意操作生效。

## API 变化

见 [api.md](api.md) 的"从 0.1.x 迁移"。要点：

- 新路径 `/v1/ip/risk`、`/v1/ip/info`、`/v1/ip/lookup`，各自带 `/batch` 和 `/keys`。
- 旧路径 `/v1/score`、`/v1/scores`、`/v1/keys` 保留，响应结构不变，OpenAPI 标记 deprecated。
- 新增只查单一供应商的能力：只查风险不消耗 ipregistry 额度，只查信息不消耗 Scamalytics 额度。
- `/v1/ip/info` 未配置时返回 `503 ipregistry_not_configured`，批量时逐项返回同一错误。

## 不变的部分

- 环境变量、Key 文件格式、Compose、Dockerfile、uvicorn 入口 `netools.app:create_app`。
- Redis key 命名空间与数据结构；升级不需要迁移或清空 Redis。
- 错误代码与 HTTP 状态码。
- 所有额度 / 并发 / 速率不变量（见 [architecture.md](architecture.md) 末尾）。

## 测试

测试目录同样按包拆分（见 [testing.md](testing.md)）。原 126 项断言全部保留并通过，新增：

- `tests/core/test_admission.py`：准入门计数与异常释放。
- `tests/lookup/test_service.py`：单域端点与合并端点共享缓存；未配置 ipregistry 时的显式错误。
- `tests/api/test_routes.py`：新旧路径的鉴权、校验、响应结构、OpenAPI deprecated 标记。
- `tests/api/test_lookup.py`：`/v1/ip/info` 直接返回 502 / 422 / 504 的路径。

## 已知遗留

- `.codex/docs/` 中的 `api.md`、`design.md`、`ipregistry.md` 仍以旧路径 `/v1/score` 描述接口；它们的额度与故障语义仍然有效。新的接口与结构说明以 `.claude/docs/` 为准。
- `tmp/api-examples.md` 面向外部调用方，使用旧路径；旧路径继续可用，可在合适时机换成 `/v1/ip/*`。
