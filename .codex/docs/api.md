# API 与配置

> 0.2.0 起接口按能力域分组为 `/v1/ip/risk`、`/v1/ip/info`、`/v1/ip/lookup`，本文描述的 `/v1/score`、`/v1/scores`、`/v1/keys` 仍可用但已标记弃用。完整的新接口说明见 [.claude/docs/api.md](../../.claude/docs/api.md)；本文的配置、环境变量和错误语义仍然有效。

## 请求认证

除 `/healthz`、`/readyz` 和交互文档 `/docs` 外，业务端点要求：

```http
Authorization: Bearer <SERVICE_API_TOKEN>
```

令牌至少 16 字符，推荐 `openssl rand -hex 32`。服务默认只通过 Compose 发布在本机 8000 端口；对外提供服务时配置 HTTPS 反向代理。

## 单 IP 查询

```http
GET /v1/score?ip=8.8.8.8
```

响应示例（示例分数不代表实时查询结果）：

```json
{
  "ip": "8.8.8.8",
  "score": 0,
  "risk": "low",
  "key_id": "eu-account-1",
  "checked_at": "2026-11-09T12:00:00Z",
  "cached": false
}
```

启用 ipregistry 后响应还包含 `ipregistry`（`data`、`key_id`、`checked_at`、`cached`）和 `ipregistry_error`，详见 [完整示例](ipregistry.md#响应与错误)。上面的旧字段示例仅展示风险部分；未配置时 `ipregistry=null`，`ipregistry_error.code=ipregistry_not_configured`。

支持 IPv4 和 IPv6，IPv6 会规范化以便缓存去重；不接受主机名、CIDR 或带 scope id 的 IPv6。缓存结果保留原始 `checked_at`，`cached` 表示本次是否从缓存读取。

## 批量查询

```http
POST /v1/scores
Content-Type: application/json

{"ips":["8.8.8.8","1.1.1.1"]}
```

最多 1000 个 IP，保留顺序和重复项。整个输入先校验，任一 IP 非法则返回 422，不查询其他项目。输入合法时返回 200，运行中的查询错误在对应项目内表达：

```json
{
  "results": [
    {
      "ip": "8.8.8.8",
      "result": {
        "ip": "8.8.8.8",
        "score": 0,
        "risk": "low",
        "key_id": "eu-account-1",
        "checked_at": "2026-11-09T12:00:00Z",
        "cached": false
      },
      "error": null
    },
    {
      "ip": "1.1.1.1",
      "result": null,
      "error": {"code":"no_key_available","message":"No API key is currently available"}
    }
  ]
}
```

批量总耗时可以超过单次查询的超时，因为每项从其 worker 开始处理时独立计时。调用方或反向代理需要设置适当的批量超时。

## 账户池状态

`GET /v1/keys` 的 `keys` 保留 Scamalytics 状态，新增 `ipregistry_keys` 数组用于 ipregistry 状态；不会调用上游。Scamalytics 部分返回各账户别名、本地 `remaining_estimate`、最后上游 `reported_remaining`、安全余量、当月预检次数、观测时间、冷却截止时间和最后错误类别。时间字段是 Unix 毫秒。未预检的余额为 `null`。余额估计包含尚未完成及失败请求的预扣，通常低于供应商显示的余额。

## 错误

单 IP 失败结构：

```json
{"error":{"code":"no_key_available","message":"No API key is currently available"}}
```

| HTTP | code | 含义 |
| --- | --- | --- |
| 401 | unauthorized | 缺少或错误的服务调用令牌 |
| 422 | FastAPI 校验响应 | 非法 IP、空批次或超过 1000 项 |
| 502 | upstream_unavailable | 正式调用失败并且无法成功切换账户 |
| 503 | no_key_available | 无可用余额、预检失败/达到上限、账户冷却或等待超时 |
| 503 | overloaded | 本进程待处理查询已满 |
| 503 | state_unavailable | Redis 访问失败 |
| 504 | lookup_timeout | 总查询期限已到 |

503 / 504 附带 `Retry-After: 1` 作为最低重试间隔建议；调用方应做有界重试和退避，额度耗尽不会一秒后自动恢复。

## Key 文件

参见 `config/keys.example.json`。`node` 保留 `eu` / `us`，可选 `api_host` 显式选择账户专属的 `api11` / `api12` / `api13`，优先于 `node` 的默认映射；不允许配置任意上游 URL。纠正 `api_host` 时保留已有 `node` 以复用 Redis 账本。账户别名 `id` 必须唯一，同一 username 也必须唯一，最多 128 个账户。

`requests_per_second` 是每账户、跨进程共享的令牌补充速率，默认 20；`max_concurrency` 是共享在途请求上限，默认 32。测试调用也计入这些上限。这里的默认值不是 Scamalytics 套餐承诺，需根据账户许可设置。配置读取于进程启动，修改文件后重启 API。

## 环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| SERVICE_API_TOKEN | 必填 | 调用本服务的独立 Bearer 令牌 |
| SCAMALYTICS_KEYS_FILE | config/keys.json | 本地凭证文件，Compose 内固定使用 secret 路径 |
| IPREGISTRY_KEYS_FILE | 未设置，不启用 | ipregistry 独立 Key 文件；空数组也表示不启用 |
| IPREGISTRY_QUOTA_SAFETY_MARGIN | 5 | ipregistry 保留不用的余额安全余量 |
| REDIS_URL | redis://localhost:6379/0 | 状态库，Compose 内固定使用 redis 服务 |
| REDIS_PREFIX | netools | 状态隔离前缀，所有共享账户实例必须一致 |
| CACHE_TTL_SECONDS | 300 | 分数缓存时间，1–86400 秒 |
| QUOTA_REFRESH_SECONDS | 86400 | 最近额度观测有效期，正式响应也会更新 |
| QUOTA_SAFETY_MARGIN | 5 | 保留不用的近似额度余量 |
| MAX_PROBES_PER_MONTH | 60 | 每账户每 UTC 月的最大预检次数，最多 250 |
| MAX_PENDING_LOOKUPS | 512 | 每进程在途查询上限，包括等待相同 IP 的请求 |
| HTTP_MAX_CONNECTIONS | 256 | 每进程上游 HTTP 连接上限 |
| LOOKUP_TIMEOUT_SECONDS | 15 | 每项查询总超时，包含预检、重试、等锁和等账户 |
| UPSTREAM_TIMEOUT_SECONDS | 5 | HTTPX 各网络阶段超时，另受总超时约束 |
| KEY_WAIT_SECONDS | 3 | 一次选取账户的等待预算，预检也可能消耗总超时 |
| LEASE_SECONDS | 30 | 租约及合并锁时长，必须大于查询总超时加 2 秒 |
| MAX_ATTEMPTS | 3 | 每项正式查询最多尝试的不同账户数 |
| BATCH_WORKERS | 32 | 每个批次的固定 worker 数量 |

表内调优配置已在 Compose 中映射。不要把刷新间隔设置成逐请求预检：会消耗测试调用预算并降低吞吐。
