# netools

通过 Scamalytics 查询 IPv4 / IPv6 的 fraud score（0–100），支持单个和批量查询、多账户 API Key 池、并发限速、近似额度预检及结果缓存。

镜像已发布到 [Docker Hub：fairylandfuture/netools](https://hub.docker.com/r/fairylandfuture/netools)。普通使用者只需要 **Docker 和 Docker Compose v2 或更新版本**。当前 `0.1.0` 镜像支持 `linux/amd64`。

## 使用 Docker Hub 镜像启动

### 1. 准备文件

将下面三个部署模板下载或复制到一个独立的 `netools` 目录，并按右侧路径保存：

| 项目中的模板 | 部署目录中的文件 |
| --- | --- |
| [deploy/compose.yaml](deploy/compose.yaml) | compose.yaml |
| [deploy/.env.example](deploy/.env.example) | .env |
| [deploy/config/keys.example.json](deploy/config/keys.example.json) | config/keys.json |

目录结构：

```text
netools/
├── compose.yaml
├── .env
└── config/
    └── keys.json
```

如果直接复制了整个 `deploy` 目录，进入该目录后执行：

```bash
cp .env.example .env
cp config/keys.example.json config/keys.json
```

Linux / macOS 上设置权限：

```bash
chmod 600 .env
chmod 700 config
chmod 644 config/keys.json
```

Key 文件单独挂载给容器中的 UID 10001 读取，宿主机的 `config` 目录通过 700 权限保护。部署使用 Docker 管理的 Redis 持久卷，启动时自动创建。

### 2. 设置服务令牌

先拉取镜像，利用镜像生成随机令牌：

```bash
docker pull fairylandfuture/netools:0.1.0
docker run --rm --entrypoint python fairylandfuture/netools:0.1.0 \
  -c 'import secrets; print(secrets.token_hex(32))'
```

编辑 `.env`，把生成的令牌填入 `SERVICE_API_TOKEN`：

```dotenv
NETOOLS_IMAGE=fairylandfuture/netools:0.1.0
SERVICE_API_TOKEN=在这里粘贴生成的随机令牌
API_BIND_HOST=127.0.0.1
API_PORT=8000
```

这个令牌用于访问本服务。至少填写 16 个字符，其他设置可保留模板默认值。

### 3. 填写 Scamalytics 凭证

编辑 `config/keys.json`，将下面的 username、key 替换为供应商提供的真实值：

```json
[
  {
    "id": "account-1",
    "username": "your_scamalytics_username",
    "key": "your_scamalytics_api_key",
    "node": "eu",
    "max_concurrency": 32,
    "requests_per_second": 20
  }
]
```

`node` 使用账户注册时绑定的 `eu` 或 `us`。多个独立账户可继续加入数组，`id` 和 username 均须唯一；每账户的并发和速率根据套餐许可调整。

### 4. 启动并确认状态

在部署目录执行：

```bash
docker compose config --quiet
docker compose pull
docker compose up -d --wait
docker compose ps
curl --fail http://127.0.0.1:8000/readyz
```

返回 `{"status":"ready"}` 表示 API 和 Redis 已就绪。部署 YAML 直接拉取已发布镜像，无需 Python、uv、Dockerfile 或项目源码。首次真实查询时才验证 Scamalytics 账户及余额。

### 5. 查询 IP

打开 `http://127.0.0.1:8000/docs`，点击 **Authorize**，填入 `.env` 中的 `SERVICE_API_TOKEN` 值，再调用接口。

单 IP：

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer 替换为SERVICE_API_TOKEN的值' \
  'http://127.0.0.1:8000/v1/score?ip=8.8.8.8'
```

批量：

```bash
curl --fail-with-body -X POST \
  -H 'Authorization: Bearer 替换为SERVICE_API_TOKEN的值' \
  -H 'Content-Type: application/json' \
  -d '{"ips":["8.8.8.8","1.1.1.1","2606:4700:4700::1111"]}' \
  http://127.0.0.1:8000/v1/scores
```

账户状态：`GET /v1/keys`，同样需要服务令牌。它返回本地别名和近似额度，不返回真实 Key。

## 远程访问、更新与维护

默认仅部署机器本机能访问。需要通过服务器 IP 访问时，将 `.env` 的 `API_BIND_HOST` 改成 `0.0.0.0`，运行 `docker compose up -d --wait`，按需配置防火墙。浏览器地址为 `http://服务器IP:8000/docs`；对外长期使用建议配置 HTTPS 反向代理。

```bash
# 查看日志
docker compose logs --tail=100 netools

# 修改 .env 后应用配置
docker compose up -d --wait

# 修改 Key 文件后重新创建 API
chmod 644 config/keys.json
docker compose up -d --force-recreate --wait netools

# 更新镜像：先将 .env 的 NETOOLS_IMAGE 改为目标版本
docker compose pull netools
docker compose up -d --wait

# 停止并移除容器，保留 Redis 数据卷
docker compose down
```

正常更新请保留 Redis 数据卷，**不要使用 `docker compose down -v`**。`restart` 不会加载修改后的环境变量，修改 `.env` 后使用 `up -d`。

完整的启动说明、配置字段、SSH 转发和故障排查见 [使用者部署文档](.codex/docs/deployment.md)。

## 开发与发布

开发环境使用 Python 3.13、uv、FastAPI、HTTPX 和 Redis。在项目根目录准备 `.env` 及 `config/keys.json` 后运行：

```bash
uv python install 3.13
uv sync --frozen
docker compose --project-name netools-dev --file compose.dev.yaml up -d --wait redis
uv run uvicorn netools.app:create_app --factory --host 127.0.0.1 --port 8000
```

VS Code 的 F5 调试和自动重载见 [编辑器说明](.codex/docs/vscode.md)。项目根目录的 `compose.yaml` 用于源码构建部署，`deploy/compose.yaml` 是独立的使用者部署模板。

发布新版本：

```bash
docker login
bash scripts/publish_dockerhub.sh fairylandfuture/netools 0.1.0
```

开发规则见 [AGENTS.md](AGENTS.md)；技术资料见[设计说明](.codex/docs/design.md)、[API 与配置](.codex/docs/api.md)、[部署与运维](.codex/docs/operations.md)及 [Docker Hub 发布说明](.codex/docs/dockerhub.md)。
