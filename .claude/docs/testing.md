# 测试与验证

测试目录镜像源码包结构；协议桩集中在 `tests/support.py`，公共 fixture 在 `tests/conftest.py`。

```text
tests/
├── conftest.py        # settings、redis、harness_factory、registry_factory、stub_state、api_harness
├── support.py         # MockScamalytics、registry_body/response/key（无真实凭证）
├── core/              # Settings 校验、AdmissionGate
├── ip_risk/           # 配置、客户端协议、Key 池 Lua、服务链路、诊断工具
├── ip_info/           # 配置、客户端协议、Key 池 Lua、服务链路
├── lookup/            # 两域合并（真实 Redis）
└── api/               # 路由契约、鉴权、弃用路径、完整 HTTP 链路（Redis 桩）
```

## 命令

不需要 Redis：

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not redis"
```

完整套件需要**专用**测试 Redis；默认地址 `redis://127.0.0.1:16379/15`，可用 `TEST_REDIS_URL` 覆盖：

```bash
docker run -d --rm --name netools-test-redis -p 127.0.0.1:16379:6379 redis:7.4-alpine
uv run pytest
docker stop netools-test-redis
```

Redis 不可达时集成测试明确失败，不会静默跳过。每个测试使用随机 `REDIS_PREFIX`，只清理自己的 key，不执行 FLUSHDB。

## 两类 harness

| fixture | Redis | 上游 | 适用 |
| --- | --- | --- | --- |
| `harness_factory` / `registry_factory` | 真实，执行生产 Lua | HTTPX MockTransport | 额度、并发、限速、单飞、取消、故障切换 |
| `api_harness` | `stub_state`（AsyncMock，Lua 返回固定 `ok`） | HTTPX MockTransport | 路由、响应结构、两域合并、脱敏、超时与取消释放 |

`api_harness` 不验证额度算法；任何改动 `*/lua/*.lua`、`core/pool.py` 或 `*/pool.py` 的变更，都要在 `tests/ip_risk/test_pool.py` 或 `tests/ip_info/test_pool.py` 增加真实 Redis 的并发/故障测试。

## 标记规则

`conftest.py` 自动给使用 `redis` fixture（含传递依赖）的测试打 `redis` 标记，不需要手工标注。

## 测试要求

- 不使用真实供应商 Key 或主机；桩里的凭证以 `synthetic` 开头，断言响应文本和异常消息中不出现它们。
- 新增错误分类时，在 `tests/<domain>/test_client.py` 的参数化表里加一行。
- 新增端点时，在 `tests/api/test_routes.py` 里补鉴权（401）、校验（422）和成功路径。
