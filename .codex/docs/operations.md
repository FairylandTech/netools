# 部署、开发与验证

直接使用镜像的用户请阅读 [使用者部署说明](deployment.md)，使用 `deploy/` 下的 Compose、环境变量及 Key 模板。发布镜像见 [Docker Hub 发布说明](dockerhub.md)。以下涉及源码构建的命令在项目根目录运行，根目录 Compose 的 `NETOOLS_IMAGE` 默认是 `netools:local`。

## Docker Compose

```bash
cp .env.example .env
cp config/keys.example.json config/keys.json
```

编辑 `.env` 中的 `SERVICE_API_TOKEN`，编辑 Key 文件并删除不使用的示例账户。每项的节点必须与注册节点一致。两份文件已经被 Git 忽略，Key 目录也被构建上下文排除。Docker secret 源文件需让容器 UID 10001 可读；共享开发机器可使用 ACL 精确授予读取权限。不要让其他非授权用户读取凭证。

```bash
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/readyz
docker compose logs --tail=100 netools
```

`/healthz` 表示进程能响应，`/readyz` 额外检查 Redis。就绪探针不调用供应商、不验证账户余额；首次正式业务查询按需预检。交互接口文档位于 `http://127.0.0.1:8000/docs`。

默认只监听宿主机回环地址。远程访问通过 HTTPS 反向代理转发；API 和 Redis 在 Compose 内网通信，Redis 无宿主机端口。镜像内不包含 uv 开发工具和源码目录之外的本地凭证，依赖由 `uv.lock` 固定。

停止 API 和 Redis 可用 `docker compose down`，保留命名数据卷。**不要在正常更新时使用 `down -v`**：删除额度状态后，下次预检仅能取得供应商的近似快照，最近预留和测试计数会丢失。

## 扩容与参数调整

单进程采用异步 I/O。可先调整账户许可范围内的 `requests_per_second` / `max_concurrency`，再根据 CPU 和 Redis 压力增加 worker 或容器。多个 worker 共用 Redis 和相同 `REDIS_PREFIX`。Compose 固定宿主机端口，不可直接 `--scale api=N`；如需多容器，先改为内部暴露并接入负载均衡器。

总供应商并发不超过所有可用账户 `max_concurrency` 之和；本进程实际出站并发还受 `HTTP_MAX_CONNECTIONS` 限制。增加 worker 不会扩大账户的共享并发、令牌或预算。每秒允许突发约一秒的令牌容量，账户合同有更严格限制时应降低配置。

监测 502 / 503 / 504 比例、`/v1/keys` 余额和冷却、Redis 内存与 AOF 健康、HTTP 延迟、文件描述符和网络连接数。Redis 默认 256 MB 且不淘汰数据；高基数 IP 会增加缓存占用，可缩短缓存或增加内存。不要使用会淘汰预算 Hash 的 LRU 策略。

生产环境使用受保护网络中的持久 Redis，按组织要求配置认证 / TLS 和备份。`REDIS_URL` 支持 `rediss://`；若修改 Compose，需要同步为 API 和 Redis 配置凭证。服务不会把 Redis 错误正文返回给客户端。

## 本地开发

VS Code 的 F5 调试与自动重载配置见 [VS Code 说明](vscode.md)。开发依赖使用独立的 `compose.dev.yaml`，向本地 API 发布 Redis 的 6379 端口。

```bash
uv python install 3.13
uv sync --frozen
docker compose --project-name netools-dev --file compose.dev.yaml up -d --wait redis
uv run uvicorn netools.app:create_app --factory --host 127.0.0.1 --port 8000
```

本地 uv 命令从 `.python-version` 选择 Python 3.13；`requires-python` 明确限制在 3.13 系列。新增依赖使用 `uv add`，提交 `pyproject.toml` 和 `uv.lock`。

## 验证

测试不会访问真实 Scamalytics，也不需要真实 Key。测试通过 HTTPX MockTransport 模拟官方结构，**使用真实 Redis 执行 Lua**，不使用掩盖原子性问题的进程内假计数。

```bash
docker run -d --rm --name netools-test-redis \
  -p 127.0.0.1:16379:6379 redis:7.4-alpine
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker stop netools-test-redis
```

也可设置 `TEST_REDIS_URL` 指向专用 Redis，默认数据库是 `redis://127.0.0.1:16379/15`。每项测试使用随机前缀，仅清理自己的 key，不执行 FLUSHDB。Redis 不可达时集成测试明确失败，避免静默跳过。

关键验证涵盖：跨池实例的原子额度预留、共享并发/限速、近似余额乱序更新、预检合并与月度上限、同 IP 并发只计费一次、Key 故障切换、HTTP 200 业务错误、IPv6、批量顺序与部分错误、请求取消后的租约回收及凭证脱敏。

## 运行限制

- 没有真实供应商凭证时只能验证协议模拟、并发不变量及容器启动，无法验证账户节点、套餐 QPS 或近似额度同步行为。
- 月中充值不会自动恢复保守预算。当前版本不提供自动补额、热加载、独立管理员权限或多租户计费。
- Redis AOF 每秒刷盘存在极端断电丢失最近预留的窗口；重启 API 不会丢额度状态，删除 Redis 数据则会。
- 各部署若使用同一供应商账户，应共享同一 Redis 和配置；其他程序直接使用账户的请求无法被本服务提前预留。
- 不对外声称固定 QPS；供应商条款中的实际吞吐限制优先。
