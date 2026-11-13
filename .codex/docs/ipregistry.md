# ipregistry 接入、配置与手动验收

当前源码在 `/v1/score` 和 `/v1/scores` 的风险结果中增加 ipregistry Single IP lookup 信息。本次不发布镜像、不运行本地 Redis、不调用真实供应商；使用旧 `0.1.0` 镜像不能验证此功能。

## 官方协议依据

- [Single IP lookup](https://ipregistry.co/docs/endpoints#single-ip)：`GET https://api.ipregistry.co/{ip}`，每次一个 IPv4 / IPv6，消耗 1 credit。本服务批量入口内部也使用 Single IP lookup，不调用上游 Batch IP API。
- [认证](https://ipregistry.co/docs/authentication)：使用 `Authorization: ApiKey ...`，避免将 Key 放进 URL。
- [响应字段](https://ipregistry.co/docs/responses/ip-address-fields)：保留 IP 类型、hostname 和 carrier、company、connection、currency、location、security、time_zone 分组。未知值可为 null；默认不请求 hostname 反查，因此 hostname 通常为 null。
- [响应头](https://ipregistry.co/docs/responses/response-headers)：从 `Ipregistry-Credits-Remaining` 读取账户剩余量，并校验 `Ipregistry-Credits-Consumed=1`；不能用 `X-Rate-Limit-Remaining` 代替账户余额。
- [错误码](https://ipregistry.co/docs/errors)：认证、额度耗尽、429、服务端错误会触发账户冷却/切换；保留地址等 IP 自身错误不切换账户。
- [额度规则](https://www.ipregistry.co/how-credits-work)：订阅余额按账期续期，充值包不失效。因此不能照搬 Scamalytics 的 UTC 自然月账本。

## 配置

从模板创建文件，填入真实凭证前确认文件已被 Git 忽略：

```bash
cp config/ipregistry-keys.example.json config/ipregistry-keys.json
```

```json
[
  {
    "id": "ipregistry-account-1",
    "account_id": "your-stable-ipregistry-account-id",
    "key": "your-ipregistry-api-key",
    "initial_credits": 1000,
    "max_concurrency": 32,
    "requests_per_second": 20
  }
]
```

- `id`：可公开的本地别名，池内唯一。
- `account_id`：运维指定的稳定账户标识，同一供应商账户始终使用同一值，只配置一个 Key；不同实例也须一致。多个 Key 可能共用同一账户余额，不能当成多个独立额度池。服务能拒绝重复 account_id / Key，但无法通过 API 验证人为起的不同别名是否实际属于同一个账户。
- `key`：只在出站请求头中使用，不写 Redis、不返回客户端。
- `initial_credits`：必填非负整数，是**首次创建 Redis 账本**时允许本服务使用的预算。示例 1000 不是供应商默认额度；应按控制台可用余额保守填写，预留其他系统的用量。后续修改此值或重启不会补充已有账本。
- `max_concurrency` / `requests_per_second`：账户在所有实例间共享的并发上限和令牌补充速率，按实际许可调整。池最多 128 个账户。

源码环境的 `.env` 增加：

```dotenv
IPREGISTRY_KEYS_FILE=config/ipregistry-keys.json
IPREGISTRY_QUOTA_SAFETY_MARGIN=5
```

不设置 `IPREGISTRY_KEYS_FILE` 或文件内容为 `[]` 时不启用 ipregistry。显式指定但不存在、无法读取或格式错误的文件会使启动失败，不会静默关闭功能。两份 Compose 模板都将文件挂载为 `/run/secrets/ipregistry_keys`，因此使用模板时文件必须存在；不启用时也要创建内容为 `[]` 的文件。真实文件已加入 `.gitignore`，config 目录原有的 `.dockerignore` 规则阻止凭证进入镜像。

## 额度与故障语义

每次出站前用 Redis Lua 原子检查冷却、余额、安全余量、并发和令牌桶，预扣 1 credit 并登记租约。首次余额来自 `initial_credits`，不会额外发起付费额度预检，也不向 ipregistry 发送 `test=1`。

成功响应只下调余额：

```text
remaining = min(local_remaining, max(0, reported_remaining - outstanding_leases))
```

失败、网络超时、取消和租约到期都不退额度；余额为近似值。同一账本内乱序响应、充值后的较大响应、进程重启、Key 轮换均不能提高余额。认证失败冷却 300 秒，429 使用 `Retry-After`，其他可重试错误冷却 30 秒。402 将余额置零。只尝试不同账户，最多 `MAX_ATTEMPTS` 次。

账本不设 TTL，也不自动按月重建。改 Key 或 `id` 时保持 `account_id`；不要通过改 `account_id`、`REDIS_PREFIX` 或清空 Redis 来绕开旧预留。额度不足时，即使上游充值或订阅续期，本地仍会停止使用该账户。当前没有自动补额/在线重置接口；补额需停用相关账户的所有调用方，等待在途请求和租约结束，备份账本并核对实际余额后，由运维更新该账户的 `remaining`。不要恢复一个未经核实的历史快照，也不要删除整个 Redis 数据库。其他程序共享账户的用量无法被本服务提前预扣。

## 响应与错误

保留原来的顶层风险字段，新增两个字段；下面只展示部分分组内字段，实际保留上游提供的各分组内容，示例不代表实时结果：

```json
{
  "ip": "8.8.8.8",
  "score": 23,
  "risk": "medium",
  "key_id": "scamalytics-account-1",
  "checked_at": "2026-11-12T12:00:00Z",
  "cached": false,
  "ipregistry": {
    "data": {
      "ip": "8.8.8.8",
      "type": "IPv4",
      "hostname": null,
      "carrier": {"name": null, "mcc": null, "mnc": null},
      "company": {"name": "Example", "domain": "example.test", "type": "hosting"},
      "connection": {"asn": 15169},
      "currency": {"code": "USD"},
      "location": {"country": {"code": "US"}},
      "security": {"is_vpn": false},
      "time_zone": {"id": "America/Los_Angeles"}
    },
    "key_id": "ipregistry-account-1",
    "checked_at": "2026-11-12T12:00:01Z",
    "cached": false
  },
  "ipregistry_error": null
}
```

两家的缓存、IP 合并锁独立，均受 `CACHE_TTL_SECONDS` 控制。顶层 `cached` 和 `checked_at` 只描述风险结果；`ipregistry` 内字段描述注册信息。两阶段共享 `LOOKUP_TIMEOUT_SECONDS` 总期限，先取得风险结果，再查询 ipregistry。

Scamalytics 失败沿用原有 HTTP 错误，不继续消耗 ipregistry 额度。已经取得风险结果后，ipregistry 失败则返回 HTTP 200、风险结果和 `ipregistry=null`，并在 `ipregistry_error` 中说明问题：

| code | 含义 |
| --- | --- |
| ipregistry_not_configured | 未启用 Key 池 |
| ipregistry_no_key_available | 余额、冷却、限流或并发原因导致没有可用账户 |
| ipregistry_upstream_unavailable | 上游失败，已尝试允许的不同账户 |
| ipregistry_unsupported_ip | 上游不支持查询此 IP，例如保留地址 |
| ipregistry_timeout | 取得风险信息后总期限耗尽 |
| state_unavailable | 取得风险信息后 Redis 出错，停止新的出站调用 |

批量接口在每项的 `result` 内返回同样结构；ipregistry 部分失败不会把已有风险数据丢掉。错误不写入结果缓存，重试时可以复用成功缓存。错误正文、原始异常、请求参数和 Key 均不透传。

`GET /v1/keys` 保留原来的 `keys` 数组，另增加 `ipregistry_keys`。包括本地别名、初始预算、估计余额、最后报告余额、安全余量、观测时间、冷却时间和错误类别，不含 account_id 或 Key。没有查询过的估计余额为 null；读取状态不调用供应商。

## 不使用 Redis 的代码验证

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not redis"
```

这些测试不启动或连接 Redis，也不访问真实供应商。API 测试使用 FastAPI 生命周期、真实服务/上游适配器、HTTPX MockTransport 和 Redis 调用桩，覆盖响应合并、IPv6、批量顺序、缓存、鉴权、故障切换、失败脱敏、超时和取消释放。桩不模拟余额算法，不代表 Lua 已经得到实际执行验证。

## 手动验证 Redis 链路

先在你选择的环境准备专用 Redis，再设置测试连接（不要用生产库）。测试资源都有随机前缀，只删除本次前缀内的 key：

```bash
TEST_REDIS_URL=redis://127.0.0.1:16379/15 uv run pytest -m redis
# 或运行包括单元测试在内的完整套件
TEST_REDIS_URL=redis://127.0.0.1:16379/15 uv run pytest
```

`tests/test_ipregistry_redis.py` 执行真实 Lua，验证多实例额度不会超分、共享并发/速率、乱序成功不补额、长期账本、冷却、租约到期、取消不退款、同 IP 单飞、Key 切换以及两家查询合并链路。Redis 不可达时明确失败，不会自动跳过。

真实上游验收另需在你选择的环境设置服务 `REDIS_URL`、两份凭证文件和 `SERVICE_API_TOKEN`，再运行 API（此命令本身不会启动 Redis）：

```bash
uv run uvicorn netools.app:create_app --factory --host 127.0.0.1 --port 8000
```

1. 查看 `/readyz`，应返回 ready；这只说明 Redis 可达。
2. 在 `/docs` 的 Authorize 中填服务令牌，调用 `/v1/score?ip=8.8.8.8`，确认 risk 与 `ipregistry.data` 同时返回且 `ipregistry_error=null`。
3. 立即再次查询，确认两处 `cached=true`，`checked_at` 不变，`/v1/keys` 两家余额不因缓存命中继续减少。
4. 查询 IPv6，再用 `/v1/scores` 提交混合 IPv4 / IPv6 和重复项，确认顺序和两类结果。
5. 在隔离测试配置中使用一个无效 ipregistry Key 和一个有效的独立账户，确认自动切换及状态冷却。所有 ipregistry Key 不可用时，确认仍得到风险数据和明确错误。
6. 真 Redis 并发测试负责验证超分、故障和取消场景；真实供应商的套餐、余额同步和限速仍以本次实测为准。
