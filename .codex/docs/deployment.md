# 使用 Docker Hub 镜像部署 netools

这套部署文件适用于直接使用已发布镜像的用户。只需安装 Docker Engine / Docker Desktop 和 Docker Compose v2 或更新版本，API 与 Redis 均由 Docker 运行。当前 `0.1.0` 镜像支持 `linux/amd64`，适用于常见 x86-64 Linux 服务器。

先确认 Docker 已启动：

```bash
docker version
docker compose version
```

## 1. 准备部署目录

将部署模板放入一个独立目录，例如 `netools`。配套部署压缩包内含本说明和以下文件；也可以从仓库 `deploy/` 目录复制这些模板：

```text
netools/
├── compose.yaml
├── .env.example
└── config/
    └── keys.example.json
```

进入该目录，创建运行配置：

```bash
cd netools
cp .env.example .env
cp config/keys.example.json config/keys.json
chmod 600 .env
chmod 700 config
chmod 644 config/keys.json
```

这些命令用于首次部署，已有配置时直接编辑原文件。`config` 目录的 700 权限保护宿主机上的凭证；单独挂载到容器的 Key 文件使用 644，让容器内 UID 10001 能读取，因而不需要调整容器用户或安装 ACL 工具。Windows / Docker Desktop 用户在编辑器中复制文件即可，以上 `chmod` 用于 Linux / macOS。

最终运行目录中还会出现 `.env` 和 `config/keys.json`。保留模板可用于核对字段。

## 2. 填写 .env

先拉取发布镜像：

```bash
docker pull fairylandfuture/netools:0.1.0
```

生成一个本服务的随机调用令牌，命令使用镜像自带的 Python：

```bash
docker run --rm --entrypoint python fairylandfuture/netools:0.1.0 \
  -c 'import secrets; print(secrets.token_hex(32))'
```

将输出填入 `.env` 的 `SERVICE_API_TOKEN`。最小配置如下：

```dotenv
NETOOLS_IMAGE=fairylandfuture/netools:0.1.0
SERVICE_API_TOKEN=在这里粘贴生成的随机令牌
API_BIND_HOST=127.0.0.1
API_PORT=8000
```

`SERVICE_API_TOKEN` 至少 16 个字符，用于调用本项目的 API。Scamalytics 提供的 API Key 填到下一步的 JSON 文件中。

其他字段可以保留模板默认值。Compose 会读取同目录 `.env`，并把需要的配置传给容器；API 内部的 Redis 地址和 Key 文件路径已由 YAML 配好。

| 常用字段 | 默认值 | 说明 |
| --- | --- | --- |
| NETOOLS_IMAGE | fairylandfuture/netools:0.1.0 | 已发布镜像，建议固定版本 |
| SERVICE_API_TOKEN | 必填 | 调用本服务的令牌 |
| API_BIND_HOST | 127.0.0.1 | 宿主机监听地址 |
| API_PORT | 8000 | 宿主机访问端口 |
| CACHE_TTL_SECONDS | 300 | 分数缓存秒数 |
| QUOTA_SAFETY_MARGIN | 5 | 预留不用的供应商额度 |
| REDIS_MAXMEMORY | 256mb | Redis 数据内存上限 |

## 3. 填写 Scamalytics 凭证

编辑 `config/keys.json`，例如：

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

请将 username 和 key 替换为供应商分配的真实凭证。`node` 填账户注册时绑定的 `eu` 或 `us`。`id` 是本地别名，只能包含英文字母、数字、下划线或连字符。

需要多个账户时，在 JSON 数组中增加对象，每项使用不同的 `id` 和 username。每个账户的并发和每秒请求数应按套餐许可填写，模板中的数值仅是本地配置默认值。服务按账户共享额度，不能把同一账号重复添加为多份余额。

## 4. 启动

在包含 `compose.yaml` 和 `.env` 的目录运行：

```bash
docker compose config --quiet
docker compose pull
docker compose up -d --wait
docker compose ps
curl --fail http://127.0.0.1:8000/readyz
```

最后应返回 `{"status":"ready"}`。`up --wait` 等待两个容器的健康检查通过。YAML 只有 `image`，不包含 `build`，启动时不会构建源码。

`/readyz` 只验证服务与 Redis；账户认证和余额在首次真实查询时检查。未填真实 Scamalytics 凭证时，就绪检查仍可能通过。

Redis 数据保存在 Docker 管理的持久卷中，首次启动会自动创建。Redis 仅在容器内网提供服务，宿主机不需要安装 Redis，也不需要预先创建数据目录。

## 5. 使用 API

本机浏览器打开 `http://127.0.0.1:8000/docs`，点击 **Authorize**，粘贴 `.env` 的 `SERVICE_API_TOKEN` 值。这里只填令牌本身，不加 `Bearer ` 前缀。

单 IP 查询：

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer 替换为SERVICE_API_TOKEN的值' \
  'http://127.0.0.1:8000/v1/score?ip=8.8.8.8'
```

批量查询：

```bash
curl --fail-with-body -X POST \
  -H 'Authorization: Bearer 替换为SERVICE_API_TOKEN的值' \
  -H 'Content-Type: application/json' \
  -d '{"ips":["8.8.8.8","1.1.1.1","2606:4700:4700::1111"]}' \
  http://127.0.0.1:8000/v1/scores
```

账户状态可通过已鉴权的 `GET /v1/keys` 查看，包含近似余额和冷却状态，不会暴露真实 Key。首次使用账户会执行额度预检；后续命中分数缓存时不调用供应商。

## 6. 从其他电脑访问

默认端口只对部署机器本机开放。需要通过服务器 IP 访问时，将 `.env` 改为：

```dotenv
API_BIND_HOST=0.0.0.0
API_PORT=8000
```

应用配置：

```bash
docker compose up -d --wait
```

在防火墙 / 云安全组中放行需要的来源 IP，然后访问 `http://服务器IP:8000/docs`。长期对外提供服务时建议通过 HTTPS 反向代理转发。修改 `API_PORT` 后，上文 URL 的端口也要对应修改。

也可以保持本机监听，通过 SSH 转发访问：

```bash
ssh -L 8000:127.0.0.1:8000 用户名@服务器IP
```

保持连接后，在自己的电脑打开 `http://127.0.0.1:8000/docs`。

## 7. 常用维护

以下命令均在部署目录执行。

查看状态与最近日志：

```bash
docker compose ps
docker compose logs --tail=100 netools
docker compose logs --tail=100 redis
```

修改 `.env` 后重新应用配置：

```bash
docker compose up -d --wait
```

修改 Key 文件后重新创建 API 容器，让凭证挂载和进程都使用新内容：

```bash
chmod 644 config/keys.json
docker compose up -d --force-recreate --wait netools
```

`docker compose restart` 不会重新读取修改后的环境变量，请使用 `up -d`。

更新镜像时，先将 `.env` 中的 `NETOOLS_IMAGE` 改为目标版本，再执行：

```bash
docker compose pull netools
docker compose up -d --wait
```

目前已发布的固定版本是 `0.1.0`，请以仓库实际标签为准。使用 `latest` 时同样需要主动执行 pull，不会自动更新正在运行的容器。

临时停止与重新启动：

```bash
docker compose stop
docker compose start --wait
```

停止并移除容器，但保留数据卷：

```bash
docker compose down
```

保留 `.env`、Key 文件、Compose 项目名称和 Redis 数据卷，重新 `up` 后继续使用已有状态。正常更新不要执行 `down -v`，它会删除持久卷中的余额预留、缓存和测试调用计数。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 提示 SERVICE_API_TOKEN 未设置 | 检查当前目录的 `.env`，该字段不能留空；`.env.example` 需要复制为 `.env` |
| API 启动时无法加载 Key 文件 | 检查 JSON、真实文件路径及文件权限；Linux 上按第 1 步设置目录与文件权限 |
| 浏览器连接被拒绝 | 检查容器是否 healthy、实际端口、监听地址和防火墙；远程访问见第 6 步 |
| HTTP 401 | 调用令牌需与 `.env` 一致，`/docs` 的 Authorize 只填令牌值 |
| HTTP 502 / 503 | 查看日志及 `/v1/keys`；核对节点、用户名、Key、余额和套餐速率 |
| 没有可用账户但供应商还有少量额度 | 默认保留 5 个 credit；如确需使用，可调整 QUOTA_SAFETY_MARGIN |
| Redis 内存不足 | 增大 REDIS_MAXMEMORY 或缩短缓存；预算状态使用 noeviction，不会被缓存淘汰 |

## 文件说明

`compose.yaml` 启动 API 和 Redis；`.env.example` 是配置模板；`config/keys.example.json` 是账户格式示例。真正的凭证保存在自己的 `.env` 和 `config/keys.json` 中。

Compose 环境变量和文件凭证的行为依据 [Docker 环境变量说明](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)和 [Compose secrets 说明](https://docs.docker.com/compose/how-tos/use-secrets/)。
