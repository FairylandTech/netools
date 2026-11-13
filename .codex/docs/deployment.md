# 使用 Docker Hub 镜像部署 netools

> 当前源码新增 ipregistry，尚未发布对应镜像。当前 Compose 还需准备 `config/ipregistry-keys.json`（从同名 example 模板复制，不启用时写 `[]`），并设置使用自行构建的新镜像。详见 [ipregistry 配置与验收](ipregistry.md)。

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

请将 username 和 key 替换为供应商分配的真实凭证。未显式指定 `api_host` 时，`node=eu` 使用 `api12`，`node=us` 使用 `api11`。`id` 是本地别名，只能包含英文字母、数字、下划线或连字符。

如果欢迎邮件通过私人链接提供凭证，请打开原邮件中的完整链接，保留 `#` 后的部分，
以页面显示的 API 用户名、API Key 和账户接口地址填写配置。不要从邮件称呼或私人
链接的随机串推导这些字段。通用文档同时列出 US、EU 两个地址，不代表账户可在
列出的节点通用。以私人凭证页面的专属示例为准；页面未明确节点时，先向供应商
确认。私人链接和真实凭证均不应发到聊天或写入日志。

### 账户专属节点为 api13

部分账户的专属示例使用 `api13`，通用文档中的两个示例不能覆盖所有账户。修复后的
源码新增可选字段 `api_host`，允许 `api11`、`api12`、`api13`，优先于 `node` 选择请求
地址。保留现有配置的 `node`、`id`、username 和 key，只增加：

```json
"api_host": "api13"
```

此字段填写主机前缀，不填写完整 URL。`api_host` 不参与 Redis 账户标识，因此纠正
请求节点不会清空已记录的额度、预检次数、冷却和并发租约。同一 username 仍不能
重复配置。以下配置只含示例凭证：

```json
[
  {
    "id": "account-1",
    "username": "your_scamalytics_username",
    "key": "your_scamalytics_api_key",
    "node": "us",
    "api_host": "api13",
    "max_concurrency": 32,
    "requests_per_second": 20
  }
]
```

已发布的原始 `0.1.0` 镜像不识别新字段，必须先使用包含修复的镜像。完整源码可在
仓库根目录构建：

```bash
docker build -t netools:api13-fix .
```

也可通过补丁 Dockerfile 复用旧镜像，仅替换配置模块和诊断工具，无需重新安装依赖：

```bash
docker build -f deploy/Dockerfile.api13-fix -t netools:api13-fix .
```

将部署目录 `.env` 中的 `NETOOLS_IMAGE` 改成 `netools:api13-fix`，填写 Key 文件中的
`api_host` 后，在部署目录执行：

```bash
docker compose up -d --force-recreate --wait netools
docker compose exec -T netools python -m netools.ip_risk.diagnostics
```

镜像名为本机构建标签，不需要对它执行 `docker compose pull`；恢复镜像版本时，也
要删除旧镜像不认识的 `api_host` 配置。补丁没有自动上传到 Docker Hub。成功预检应
显示 `api_host=api13`、`result=ok` 和非空余额。已有协议错误的冷却最多再保留 30 秒，
余额只减不增规则保持不变。

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

### `no_key_available` 且 `last_error=protocol`

如果 `/v1/keys` 的余额与观测时间均为 `null`、预检次数大于零且最后错误是
`protocol`，表示账户已尝试额度预检，但尚未取得符合协议的成功响应。它不能证明
余额已用完，也不能单凭这个错误认定凭证正确。非 JSON、重定向、404 和缺失或类型
不匹配的响应字段都可能被归入此错误。

源码仓库提供安全诊断工具，只输出 HTTP 状态、固定枚举和字段类型，不输出用户名、
API Key、上游 URL、原始异常、响应错误正文或任意响应字段值。它通过原有 Redis Lua
取得预检租约，沿用冷却、速率、并发、月度预检预算和余额只减不增规则。每个账户
最多尝试一次 `test=1`，不会发起正式评分查询；本次预检也计入月度测试预算。

使用包含该工具的新镜像时，在部署目录运行：

```bash
docker compose exec -T netools python -m netools.ip_risk.diagnostics
```

使用现有 `0.1.0` 镜像无需重建。取得源码中的 `src/netools/ip_risk/diagnostics.py` 文件后，
可将其通过标准输入交给容器运行。例如，从源码仓库的 `deploy/` 目录执行：

```bash
docker compose exec -T netools python - < ../src/netools/ip_risk/diagnostics.py
```

独立部署目录可将重定向右侧替换为下载到本机的该文件路径，脚本自动读取容器内的
配置和凭证。`reservation=cooldown` 时不会访问上游，待冷却结束后再执行；协议错误
默认冷却 30 秒。`fresh` 表示已有新鲜额度快照；`busy` 表示预检锁、并发或速率暂时
不允许；`probe_limit` 表示当月预检预算已用完。

重点查看 `accounts[].response`：

| 诊断结果 | 下一步 |
| --- | --- |
| `http_status=404` 且 `format=non_json` | 优先核对供应商 URL 路径中的 username、账户绑定节点及 API 版本；仍一致时排查出站代理或供应商路由 |
| `http_status` 为 301 / 302，或其他 `format=non_json` | 核对供应商提供的接口地址和出站网络中的代理行为 |
| `validation_errors` 出现 `mode` / `status` / `credits.remaining` | 按字段路径核对接口版本和实际结构，不要绕过正式响应的严格校验 |
| `result=authentication` | 核对供应商分配的 username、key 和注册节点 |
| `result=network` | 检查容器 DNS、TLS、出站连接和超时 |

其中 `id` 是可自行命名的本地账户别名，`username` 必须使用供应商给出的 API 路径
账户标识，两者用途不同。当前仅支持 v3；未指定 `api_host` 时，`node=us` 对应
`api11`，`node=eu` 对应 `api12`；专属节点如 `api13` 使用上面的显式配置。节点以
供应商专属账户示例为准，不要按部署服务器所在地选择，也不要反复切换节点试探。
若供应商提供其他版本，应先核实协议兼容性。
修改部署目录的 `config/keys.json` 后，按维护章节重新创建 API 容器，再运行诊断。

将诊断 JSON 用于排查即可。不要为了排查开启 HTTPX 请求调试日志或清空 Redis；也
不要循环运行诊断，以免耗尽月度测试预算。`last_error` 保留历史错误，即使后来成功
也可能仍显示旧值，应结合本次 `result`、余额和观测时间判断。

## 文件说明

`compose.yaml` 启动 API 和 Redis；`.env.example` 是配置模板；`config/keys.example.json` 是账户格式示例。真正的凭证保存在自己的 `.env` 和 `config/keys.json` 中。

Compose 环境变量和文件凭证的行为依据 [Docker 环境变量说明](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)和 [Compose secrets 说明](https://docs.docker.com/compose/how-tos/use-secrets/)。
