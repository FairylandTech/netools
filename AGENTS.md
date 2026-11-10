# Project instructions

本项目使用 Python 3.13、uv、FastAPI、HTTPX 和 Redis；项目设计与运行文档位于 `.codex/docs`。

- 先阅读 `.codex/docs/design.md`；部署与验证方法见 `.codex/docs/operations.md`。
- 使用 `uv sync --frozen` 安装依赖；依赖变化后更新并提交 `uv.lock`。
- 所有出站 HTTP 与 Redis 操作必须异步；共享 HTTPX 客户端，不在请求内新建连接池。
- Key 池额度、速率与并发预留通过 Redis Lua 原子执行；不得改成进程内计数。
- Scamalytics 用户名、Key、节点是一组凭证；同一账户不能作为多个独立额度池。
- 不记录上游 URL、请求参数、原始异常或响应错误正文；它们可能包含 API Key。
- 额度为近似值；同月不能用滞后的响应增加本地剩余额度。失败和取消不返还预留额度。
- `test=1` 仅用于额度预检，测试分数不能作为真实分数返回；不要逐 IP 发测试请求。
- `.env` 和 `config/keys.json` 不入库，不把真实凭证放进镜像或测试。
- 修改并发、额度、重试或租约逻辑时，补充有意义的并发/故障测试。
- 验证命令：`uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`。
- 集成测试使用独立 Redis（默认 `redis://127.0.0.1:16379/15`），所有测试资源使用随机前缀。
- 文档使用中文，代码标识符和错误代码使用英文。
