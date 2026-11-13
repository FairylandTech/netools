# 代码结构与分层

netools 提供两类 IP 能力：**风险评分**（Scamalytics）和**地址信息**（ipregistry）。0.2.0 起源码按"能力域"分包，每个域自带配置、上游客户端、Key 池、Lua 脚本、模型和服务；跨域共用的基础设施放在 `core`，HTTP 层放在 `api`，跨域编排放在 `lookup`。

## 目录

```text
src/netools/
├── __init__.py            # __version__
├── app.py                 # create_app：生命周期、依赖装配、挂载路由
├── core/                  # 共享基础设施（不依赖任何业务域）
│   ├── config.py          # Settings：环境变量 / .env
│   ├── errors.py          # ServiceError、ErrorDetail
│   ├── ip.py              # normalize_ip、IPAddress
│   ├── http.py            # retry_after
│   ├── admission.py       # AdmissionGate：每进程在途查询上限
│   ├── cache.py           # SingleFlightCache：结果缓存 + 同 IP 合并锁
│   ├── pool.py            # KeyPool：账户轮询与等待；预留由子类的 Lua 决定
│   ├── scripts.py         # read_lua
│   └── lua/unlock.lua     # 比较 token 后删除锁
├── ip_risk/               # 风险评分域（Scamalytics v3）
│   ├── config.py          # ScamalyticsKeyConfig、load_scamalytics_keys
│   ├── models.py          # Risk、RiskScore
│   ├── client.py          # ScamalyticsClient、ScamalyticsError、Credits
│   ├── pool.py            # ScamalyticsKeyPool、Lease（月度账本 + test=1 预检）
│   ├── service.py         # RiskService：缓存 + 单飞 + 账户切换
│   ├── diagnostics.py     # python -m netools.ip_risk.diagnostics
│   └── lua/reserve.lua, finish.lua
├── ip_info/               # 地址信息域（ipregistry）
│   ├── config.py          # IpregistryKeyConfig、load_ipregistry_keys
│   ├── models.py          # IpInfoData、IpInfo
│   ├── client.py          # IpregistryClient、IpregistryError
│   ├── pool.py            # IpregistryKeyPool、Lease（长期账本，无月度重置）
│   ├── service.py         # InfoService
│   └── lua/reserve.lua, finish.lua
├── lookup/                # 请求级编排
│   ├── models.py          # IpLookup、BatchRequest、BatchItem[T]、BatchResponse[T]
│   └── service.py         # LookupService：准入、总超时、批量、两域合并
└── api/                   # HTTP 层
    ├── deps.py            # Bearer 鉴权、app.state 依赖注入
    ├── errors.py          # ServiceError / RedisError → JSON 错误体
    ├── system.py          # /healthz、/readyz
    └── v1/
        ├── __init__.py    # /v1 路由聚合
        ├── risk.py        # /v1/ip/risk*
        ├── info.py        # /v1/ip/info*
        ├── lookup.py      # /v1/ip/lookup*
        └── legacy.py      # /v1/score、/v1/scores、/v1/keys（已弃用）
```

## 依赖方向

```text
api  →  lookup  →  ip_risk / ip_info  →  core
app  →  以上全部（仅在 create_app 中装配）
```

- `core` 不得导入任何业务域或 `api`。
- `ip_risk` 与 `ip_info` 互不导入；它们不知道对方存在。
- `lookup` 是唯一同时依赖两个域的包；新的跨域组合写在这里。
- `api` 只做参数校验、鉴权和模型转换，不包含业务判断。
- 所有对象在 `app.py` 的 lifespan 中装配并挂到 `app.state`；路由通过 `api/deps.py` 取用。

## 各层职责

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| `*/client.py` | 一次上游 HTTP 调用、协议校验、错误分类、凭证脱敏 | 重试、账户选择、缓存 |
| `*/pool.py` | 账户状态 key、Lua 预留/释放、快照 | HTTP 调用（Scamalytics 预检除外） |
| `*/service.py` | 单个 IP：缓存、单飞锁、账户切换、`ServiceError` | 准入、总超时、批量 |
| `lookup/service.py` | 准入门、`LOOKUP_TIMEOUT_SECONDS`、Redis 错误映射、批量 worker、风险+信息合并 | 任何供应商协议 |
| `api/` | 路径、鉴权、响应模型、弃用标记 | 业务逻辑 |

两个域服务都把"缓存 + 同 IP 合并"交给 `core.cache.SingleFlightCache`，把"轮询 + 等待 + 排除失败账户"交给 `core.pool.KeyPool`。域内只实现 `try_reserve`、`finish`、`state_keys`、`snapshots` 和 `_fetch`。

## 命名约定

- 供应商专属对象以供应商命名：`ScamalyticsClient`、`IpregistryKeyPool`、`IpregistryError`。
- 对外能力以能力命名：`RiskService` / `RiskScore`、`InfoService` / `IpInfo`、`LookupService` / `IpLookup`。
- 错误代码保持 0.1.x 的值不变：风险域无前缀（`no_key_available`、`upstream_unavailable`），信息域以 `ipregistry_` 前缀，基础设施为 `overloaded`、`state_unavailable`、`lookup_timeout`、`unauthorized`。
- 文档用中文，标识符、错误代码和日志用英文。

## Redis 命名空间（与 0.1.x 完全一致）

| 用途 | Key |
| --- | --- |
| 风险结果缓存 | `{prefix}:score:{sha256(ip)}` |
| 风险合并锁 | `{prefix}:lookup:{sha256(ip)}` |
| Scamalytics 账户月度状态 | `{prefix}:{{{account_hash}}}:{YYYYMM}` |
| Scamalytics 共享租约 | `{prefix}:{{{account_hash}}}:leases` |
| Scamalytics 预检锁 | `{prefix}:{{{account_hash}}}:{YYYYMM}:probe` |
| 信息结果缓存 | `{prefix}:ipregistry:result:{sha256(ip)}` |
| 信息合并锁 | `{prefix}:ipregistry:lookup:{sha256(ip)}` |
| ipregistry 账户状态 | `{prefix}:{{ipregistry:{account_hash}}}:state` |
| ipregistry 共享租约 | `{prefix}:{{ipregistry:{account_hash}}}:leases` |

升级到 0.2.0 不需要迁移 Redis 数据；额度账本、冷却和缓存全部沿用。

## 新增一个供应商 / 能力域

1. 建包 `src/netools/<capability>/`，放 `config.py`（Key 模型 + 加载函数）、`client.py`、`pool.py`（继承 `core.pool.KeyPool`，自带 `lua/`）、`models.py`、`service.py`（用 `SingleFlightCache`）。
2. 在 `core/config.py` 的 `Settings` 增加 Key 文件路径与域专属参数；凭证文件进 `.gitignore` 并提供 `config/*.example.json`。
3. 在 `app.py` 装配并挂到 `app.state`；在 `api/deps.py` 增加依赖；在 `api/v1/` 增加 `<capability>.py`，前缀 `/ip/<capability>`，至少提供 `GET ""`、`POST /batch`、`GET /keys`。
4. 需要和其他域合并时，只改 `lookup/`。
5. 测试放在 `tests/<capability>/`，协议桩放在 `tests/support.py`；涉及 Lua 的用真实 Redis。

## 不变量（修改前先读）

- 额度、并发、速率的决定权在 Lua（`*/lua/reserve.lua`），Python 侧不做进程内计数。
- 同一账本内余额只减不增；失败、取消、超时不返还预留。
- `test=1` 只用于额度预检，预检结果不能当作真实分数。
- `SingleFlightCache` 只缓存成功结果；`ServiceError` 不写缓存。
- 合并查询先风险后信息：风险失败直接返回 HTTP 错误，不消耗 ipregistry 额度；信息失败只写进 `info_error`。
- 上游 URL、请求参数、原始异常、响应错误正文都可能含 Key，不进日志、不进错误消息、不进响应。
