# HTTP API（v1）

所有路径以能力域分组：`/v1/ip/risk`（风险评分）、`/v1/ip/info`（地址信息）、`/v1/ip/lookup`（两者合并）。每个域下结构相同：`GET` 查单个 IP，`POST .../batch` 批量，`GET .../keys` 看账户池状态。0.1.x 的 `/v1/score`、`/v1/scores`、`/v1/keys` 仍可用，但在 OpenAPI 中标记为 deprecated。

交互文档在 `/docs`，OpenAPI 在 `/openapi.json`。

## 认证

除 `/healthz`、`/readyz`、`/docs`、`/openapi.json` 外，所有端点要求：

```http
Authorization: Bearer <SERVICE_API_TOKEN>
```

缺少或错误时返回 `401`，附 `WWW-Authenticate: Bearer`。

## 路径总览

| 方法 | 路径 | 说明 | 响应模型 |
| --- | --- | --- | --- |
| GET | `/healthz` | 进程存活 | `{"status":"ok"}` |
| GET | `/readyz` | Redis 可达 | `{"status":"ready"}` |
| GET | `/v1/ip/risk?ip=` | Scamalytics 风险评分 | `RiskScore` |
| POST | `/v1/ip/risk/batch` | 批量风险评分 | `BatchResponse[RiskScore]` |
| GET | `/v1/ip/risk/keys` | Scamalytics 账户池状态 | `list[KeySnapshot]` |
| GET | `/v1/ip/info?ip=` | ipregistry 地址信息 | `IpInfo` |
| POST | `/v1/ip/info/batch` | 批量地址信息 | `BatchResponse[IpInfo]` |
| GET | `/v1/ip/info/keys` | ipregistry 账户池状态 | `list[KeySnapshot]` |
| GET | `/v1/ip/lookup?ip=` | 风险 + 信息 | `IpLookup` |
| POST | `/v1/ip/lookup/batch` | 批量合并 | `BatchResponse[IpLookup]` |
| GET | `/v1/score?ip=` | **弃用**，等价 `/v1/ip/lookup`，旧扁平结构 | `LegacyScore` |
| POST | `/v1/scores` | **弃用**，等价 `/v1/ip/lookup/batch` | `BatchResponse[LegacyScore]` |
| GET | `/v1/keys` | **弃用**，`{"keys": [...], "ipregistry_keys": [...]}` | 对象 |

IP 通过查询参数传递，支持 IPv4 / IPv6；IPv6 会规范化（`2606:4700:4700:0:0:0:0:1111` → `2606:4700:4700::1111`）。不接受主机名、CIDR 或带 scope id 的地址，非法值返回 `422`。

## 单个 IP

### `GET /v1/ip/risk?ip=8.8.8.8`

```json
{
  "ip": "8.8.8.8",
  "score": 23,
  "risk": "medium",
  "key_id": "scamalytics-account-1",
  "checked_at": "2026-11-12T12:00:00Z",
  "cached": false
}
```

| 字段 | 说明 |
| --- | --- |
| `score` | 0–100，越高风险越大 |
| `risk` | `low` / `medium` / `high` / `very high` |
| `key_id` | 命中的本地账户别名，不是上游凭证 |
| `checked_at` | 上游查询时间；缓存命中时保留首次时间 |
| `cached` | 本次是否来自 Redis 缓存（不消耗额度） |

### `GET /v1/ip/info?ip=8.8.8.8`

```json
{
  "data": {
    "ip": "8.8.8.8",
    "type": "IPv4",
    "hostname": null,
    "carrier": {"name": null, "mcc": null, "mnc": null},
    "company": {"name": "Example", "domain": "example.test", "type": "hosting"},
    "connection": {"asn": 15169, "route": "8.8.8.0/24"},
    "currency": {"code": "USD"},
    "location": {"country": {"code": "US"}},
    "security": {"is_vpn": false},
    "time_zone": {"id": "America/Los_Angeles"}
  },
  "key_id": "ipregistry-account-1",
  "checked_at": "2026-11-12T12:00:01Z",
  "cached": false
}
```

`data` 内各分组按上游原样保留，未知值为 `null`；只暴露 `ip`、`type`、`hostname` 和七个分组，错误/调试字段被丢弃。未配置 ipregistry 时返回 `503 ipregistry_not_configured`。

### `GET /v1/ip/lookup?ip=8.8.8.8`

```json
{
  "ip": "8.8.8.8",
  "risk": { "ip": "8.8.8.8", "score": 23, "risk": "medium", "key_id": "...", "checked_at": "...", "cached": false },
  "info": { "data": { "...": "..." }, "key_id": "...", "checked_at": "...", "cached": false },
  "info_error": null
}
```

语义：**风险是必需的，信息是尽力而为的**。

- 风险查询失败 → 直接返回对应 HTTP 错误（见下），不消耗 ipregistry 额度。
- 风险成功、信息失败 → `200`，`info=null`，`info_error` 说明原因。
- 两个阶段共用 `LOOKUP_TIMEOUT_SECONDS`；先风险后信息。
- `risk.cached` 与 `info.cached` 各自独立，两家缓存互不影响。

`info_error.code` 可能的值：

| code | 含义 |
| --- | --- |
| `ipregistry_not_configured` | 未启用 ipregistry Key 池 |
| `ipregistry_no_key_available` | 余额、冷却、限流或并发原因没有可用账户 |
| `ipregistry_upstream_unavailable` | 上游失败，已尝试允许的不同账户 |
| `ipregistry_unsupported_ip` | 上游不支持此 IP（保留地址等） |
| `ipregistry_timeout` | 取得风险后总期限耗尽 |
| `state_unavailable` | 取得风险后 Redis 出错 |

## 批量

三个 `.../batch` 端点请求体相同：

```json
{"ips": ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"]}
```

- 1–1000 个 IP，保留顺序和重复项；全部先校验，任一非法返回 `422`，不发起任何上游调用。
- 校验通过后固定返回 `200`，逐项成功或失败：

```json
{
  "results": [
    {"ip": "8.8.8.8", "result": { "...": "..." }, "error": null},
    {"ip": "1.1.1.1", "result": null, "error": {"code": "no_key_available", "message": "No API key is currently available"}}
  ]
}
```

`result` 的类型随端点变化（`RiskScore` / `IpInfo` / `IpLookup`）。每项独立计时，整批耗时可以超过单次超时；调用方应设置充裕的客户端超时。每批只创建 `BATCH_WORKERS` 个 worker，相同 IP 合并为一次上游调用。

## 账户池状态

`GET /v1/ip/risk/keys`：

```json
[
  {
    "id": "scamalytics-account-1",
    "remaining_estimate": 4980,
    "reported_remaining": 4982,
    "safety_margin": 5,
    "probes_this_month": 1,
    "observed_at_unix_ms": 1794282765000,
    "blocked_until_unix_ms": null,
    "last_error": null
  }
]
```

`GET /v1/ip/info/keys` 结构相同，去掉 `probes_this_month`，增加 `initial_credits`。两者只读本地 Redis 状态，不调用供应商，不返回 username、account_id 或 Key。`remaining_estimate` 已扣除在途与失败请求的预留，通常低于供应商控制台；未预检/未使用时为 `null`。

## 错误

非 422 的错误统一结构：

```json
{"error": {"code": "no_key_available", "message": "No API key is currently available"}}
```

| HTTP | code | 端点 | 含义 |
| --- | --- | --- | --- |
| 401 | `unauthorized` | 全部 | 令牌缺失或错误 |
| 422 | FastAPI 校验体 | 全部 | 非法 IP、空批次、超过 1000 项 |
| 422 | `ipregistry_unsupported_ip` | `/ip/info` | 上游不支持此 IP |
| 502 | `upstream_unavailable` | `/ip/risk`、`/ip/lookup`、legacy | Scamalytics 失败且切换账户无效 |
| 502 | `ipregistry_upstream_unavailable` | `/ip/info` | ipregistry 失败且切换账户无效 |
| 503 | `no_key_available` | `/ip/risk`、`/ip/lookup`、legacy | 无可用 Scamalytics 账户 |
| 503 | `ipregistry_no_key_available` | `/ip/info` | 无可用 ipregistry 账户 |
| 503 | `ipregistry_not_configured` | `/ip/info` | 未启用 ipregistry |
| 503 | `overloaded` | 全部查询 | 本进程在途查询达到 `MAX_PENDING_LOOKUPS` |
| 503 | `state_unavailable` | 全部 | Redis 不可用 |
| 504 | `lookup_timeout` | 全部查询 | 超过 `LOOKUP_TIMEOUT_SECONDS` |

503 / 504 附带 `Retry-After: 1`，表示最低重试间隔而非恢复承诺；额度耗尽不会一秒后恢复。

## 从 0.1.x 迁移

| 旧 | 新 | 差异 |
| --- | --- | --- |
| `GET /v1/score` | `GET /v1/ip/lookup` | 风险字段移入 `risk`，`ipregistry` → `info`，`ipregistry_error` → `info_error` |
| `POST /v1/scores` | `POST /v1/ip/lookup/batch` | 同上，位于每项 `result` 内 |
| `GET /v1/keys` | `GET /v1/ip/risk/keys` + `GET /v1/ip/info/keys` | 两个数组分开返回 |
| 无 | `GET /v1/ip/risk`、`POST /v1/ip/risk/batch` | 只查风险，不碰 ipregistry 额度 |
| 无 | `GET /v1/ip/info`、`POST /v1/ip/info/batch` | 只查地址信息，不碰 Scamalytics 额度 |

旧路径的行为、响应结构和错误代码与 0.1.x 一致，可继续使用；新接入请直接使用 `/v1/ip/*`。旧路径计划在下一个主版本移除。

## 示例

```bash
TOKEN='替换为 SERVICE_API_TOKEN'
BASE='http://127.0.0.1:8000'

curl --fail-with-body -H "Authorization: Bearer $TOKEN" "$BASE/v1/ip/risk?ip=8.8.8.8"
curl --fail-with-body -H "Authorization: Bearer $TOKEN" "$BASE/v1/ip/info?ip=8.8.8.8"
curl --fail-with-body -G -H "Authorization: Bearer $TOKEN" \
  --data-urlencode 'ip=2606:4700:4700::1111' "$BASE/v1/ip/lookup"
curl --fail-with-body -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"ips":["8.8.8.8","1.1.1.1"]}' "$BASE/v1/ip/lookup/batch"
curl --fail-with-body -H "Authorization: Bearer $TOKEN" "$BASE/v1/ip/risk/keys"
```
