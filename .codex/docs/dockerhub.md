# 发布到 Docker Hub

仓库中的 `Dockerfile` 已支持发布独立 API 镜像。Redis 保持独立容器运行，Scamalytics 凭证和服务令牌在运行时配置。

## 当前发布

已发布至 [fairylandfuture/netools](https://hub.docker.com/r/fairylandfuture/netools)：

- 标签：`0.1.0`、`latest`，两者指向同一镜像。
- 运行平台：`linux/amd64`。
- 远程镜像 digest：`sha256:d55cfad195c6724307d871a8f993d871fa6608d6728f1e32a6a685c329c33f16`。
- 已验证本地镜像构建、非 root 运行、Redis 就绪、服务鉴权、上游 Lua 资源打包和远程 manifest。

```bash
docker pull fairylandfuture/netools:0.1.0
```

## 登录与目标仓库

当前仓库为 `fairylandfuture/netools`。如需发布到其他账户或组织，请先在 Docker Hub 创建或选择有推送权限的目标仓库，再替换下方命令中的镜像名称。

在本机终端登录：

```bash
docker login
```

Docker Hub 支持浏览器设备码登录。也可以运行 `docker login --username fairylandfuture` 并在交互提示中输入有推送权限的访问令牌。登录凭证由 Docker 管理，不放入项目 `.env`、脚本、Dockerfile 或聊天。

## 发布当前架构镜像

项目当前版本为 `0.1.0`。从项目根目录运行：

```bash
bash scripts/publish_dockerhub.sh fairylandfuture/netools 0.1.0
```

脚本会构建当前机器架构的镜像，推送 `0.1.0` 和 `latest` 两个标签，然后读取远程 manifest 验证发布结果。脚本也可以从任意工作目录通过绝对路径调用。当前开发服务器为 `linux/amd64`。

对应的手工命令：

```bash
docker build --pull -t fairylandfuture/netools:0.1.0 -t fairylandfuture/netools:latest .
docker push fairylandfuture/netools:0.1.0
docker push fairylandfuture/netools:latest
docker buildx imagetools inspect fairylandfuture/netools:0.1.0
```

建议部署时固定版本标签，后续更新使用新版本号。

## 从 Docker Hub 部署

普通使用者请使用 `deploy/` 下的独立部署模板，完整步骤见 [使用者部署说明](deployment.md)。只需 Compose 文件、`.env` 和 `config/keys.json` 即可启动，模板包含 API、Redis、健康检查和持久卷。

进入独立部署目录，填写服务令牌与供应商凭证，将 `.env` 中的镜像配置为已发布版本：

```dotenv
NETOOLS_IMAGE=fairylandfuture/netools:0.1.0
```

按部署说明设置 Key 文件权限后启动：

```bash
docker compose pull
docker compose up -d --wait
curl --fail http://127.0.0.1:8000/readyz
```

部署模板不包含 `build`，Redis 使用 Docker 自动管理的数据卷。项目根目录的 Compose 保留源码构建和原有宿主机数据目录配置，供开发部署使用。私有仓库需要在部署机器先登录有拉取权限的 Docker Hub 账户。

## 多架构发布（可选）

需要同时部署到 x86-64 和 ARM64 时，使用支持对应平台的 buildx builder：

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t fairylandfuture/netools:0.1.0 \
  -t fairylandfuture/netools:latest \
  --push .
```

如果当前 builder 不支持目标平台，需要配置具备对应原生节点或模拟能力的 builder。多架构构建尚未在本项目验证，不能用单架构构建结果代替确认。

## 镜像内容

构建使用 `uv sync --frozen` 安装 Python 3.13 依赖，运行用户为 UID 10001。Dockerfile 仅复制依赖声明、README 和 `src/`；`.dockerignore` 排除了 `.env`、Key 文件目录、Git、虚拟环境和编辑器目录。镜像中不包含本机运行凭证。

参考：[推送镜像](https://docs.docker.com/docker-hub/repos/manage/hub-images/push/)、[Docker 登录](https://docs.docker.com/reference/cli/docker/login/)、[多架构构建](https://docs.docker.com/build/building/multi-platform/)。
