# 在 VS Code 中运行 API

项目已提供 `.vscode/launch.json`、`tasks.json`、`settings.json` 和推荐扩展。API 在本地 Python 3.13 虚拟环境中运行，Redis 由独立的 `compose.dev.yaml` 启动。

## 首次准备

1. 在 VS Code 中打开整个 `netools` 文件夹。
2. 安装推荐的 **Python**（`ms-python.python`）和 **Python Debugger**（`ms-python.debugpy`）扩展。使用 Remote SSH / WSL 时，将扩展安装在对应的远程环境中；uv 和 Docker 命令也需要在该环境中可用。
3. 在 VS Code 集成终端执行：

   ```bash
   uv python install 3.13
   uv sync --frozen
   ```

4. 如果尚未创建本地配置，从 `.env.example` 复制为 `.env`，从 `config/keys.example.json` 复制为 `config/keys.json`。已有文件无需重新复制。
5. 在 `.env` 中设置自己的 `SERVICE_API_TOKEN`，在 `config/keys.json` 中填写真实 Scamalytics 账户并删除不用的示例项。`SERVICE_API_TOKEN` 是调用本项目 API 时使用的令牌，与 Scamalytics 提供的 Key 分开；可以用 `openssl rand -hex 32` 生成，再填到 `.env`。保持本地连接配置：

   ```dotenv
   SCAMALYTICS_KEYS_FILE=config/keys.json
   REDIS_URL=redis://127.0.0.1:6379/0
   ```

6. 通过命令面板的 **Python: Select Interpreter** 选择项目 `.venv` 中的 Python 3.13。Linux / macOS 为 `.venv/bin/python`，Windows 为 `.venv\Scripts\python.exe`。

## F5 启动与断点

完成首次准备后，在左侧“运行和调试”中选择 **API: 调试（F5）**，按 F5。启动前会顺序执行 `uv sync --frozen`、启动开发 Redis，并等待健康检查通过；随后由 uvicorn 加载 `netools.app:create_app` 工厂运行 API。

打开：

- `http://127.0.0.1:8000/docs`：交互 API 文档。
- `http://127.0.0.1:8000/readyz`：Redis 就绪检查。

在 `/docs` 中点击 **Authorize**，只输入 `.env` 的 `SERVICE_API_TOKEN` 值，然后调用 `/v1/ip/risk`、`/v1/ip/info` 或 `/v1/ip/lookup`。可以在 `src/netools/api/v1/` 的路由或 `src/netools/lookup/service.py`、`src/netools/ip_risk/service.py` 中设置断点，发送对应请求时会暂停。

配置固定启动 uvicorn 模块，当前打开 Dockerfile 或其他文件均不影响 F5。使用 **Shift+F5** 停止 API；Redis 会继续运行，便于下一次调试。需要停止 Redis 时，从命令面板 **Tasks: Run Task** 选择 **Redis: stop**，数据卷继续保留。

## 保存后自动重载

选择 **API: 开发（自动重载）** 再按 F5。修改 `src/` 中的 Python 文件后，uvicorn 会重启 API；已启用子进程调试。修改 `.env` 或 Key 文件后，需要停止并重新启动调试。

## 常见问题

- **`service_api_token` / `Field required`**：启动时没有从环境变量或根目录 `.env` 读到 `SERVICE_API_TOKEN`。确认已经完成首次配置，且终端当前目录为项目根目录。激活 `.venv` 不会自动创建 `.env`；只有 `.env.example` 也不够。
- **找不到 uv / docker**：确认 VS Code 所在环境的 PATH 能找到命令，Docker 已启动。刚安装工具时可重启 VS Code。
- **8000 端口占用**：停止已有 API，或在 `launch.json` 中修改 `--port` 后的值。若先前启动过完整 Compose，可用 `docker compose stop api` 停止容器中的 API。
- **6379 端口占用**：已有 Redis 可以直接使用；在两个 launch 配置中将 `preLaunchTask` 改成 `uv: sync`，再按需修改 `.env` 的 `REDIS_URL`。原先手工启动的 `netools-dev-redis` 与新开发 Compose 会竞争同一端口。
- **连接 Redis 失败**：本地 API 使用 `127.0.0.1`，Docker 内部的 `redis` 主机名用于完整 Compose 部署。
- **需要纯命令行启动开发依赖**：执行 `docker compose --project-name netools-dev --file compose.dev.yaml up -d --wait redis`。

配置字段依据 [VS Code Python 调试文档](https://code.visualstudio.com/docs/python/debugging)；任务依赖顺序依据 [VS Code Tasks 文档](https://code.visualstudio.com/docs/debugtest/tasks)。

## 从终端启动

先完成上面的首次准备，再从项目根目录执行：

```bash
docker compose --project-name netools-dev --file compose.dev.yaml up -d --wait redis
uv run uvicorn netools.app:create_app --factory --host 127.0.0.1 --port 8000
```

应用会读取当前目录的 `.env`。已激活项目虚拟环境时，也可以使用 `python -m uvicorn netools.app:create_app --factory`；直接执行这条命令不会替你创建配置或启动 Redis。若仍在使用示例 Scamalytics 凭证，服务可以启动，但真实 IP 查询需要先填写有效凭证。
