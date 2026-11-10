# 实现验证记录

本地使用 uv 管理的 Python 3.13.18、独立 Redis 7.4 容器，以及 HTTPX MockTransport 模拟 Scamalytics 进行验证。

| 检查 | 结果 |
| --- | --- |
| `uv lock --check` | 依赖声明与 lock 一致 |
| `uv run ruff check .` | 通过 |
| `uv run ruff format --check .` | 通过 |
| `uv run pytest -q` | 35 项通过 |
| Docker 多阶段构建 | 通过 |
| Compose 配置校验 | 通过 |
| 容器启动与 `/readyz` | 通过 |
| 运行用户 | UID 10001 |
| 容器 Python | 3.13.18 |
| 鉴权后读取未预检的 Key 池 | 通过，无上游调用 |

并发测试包括两个独立池/服务对象共享 Redis 的场景：

- 300 个同时预留竞争 100 个额度，恰好 100 个预留成功。
- 200 个相同 IP 查询，合计一次额度预检和一次正式请求。
- 200 个不同 IP 查询，在账户并发上限 4 下完成且没有突破限制。
- 月份切换重新预检预算，并跨月保留同一账户的在途并发限制。

没有使用真实供应商 Key，也没有调用正式供应商服务。以上结果不代表真实套餐 QPS、网络延迟或计费同步精度。


## Docker Hub 使用者部署验证

在独立临时目录中，仅复制 `deploy/compose.yaml`、环境变量模板和 Key 模板进行验证；目录不含 Dockerfile、项目源码或 Python 依赖声明。

- 使用空 Docker 客户端配置，匿名拉取已发布的 `fairylandfuture/netools:0.1.0` 和 Redis 镜像成功。
- `SERVICE_API_TOKEN` 为空时，Compose 在启动前明确拒绝配置。
- 配置令牌后，`docker compose up -d --wait` 成功，API 和 Redis 均 healthy。
- 使用宿主机 config 目录 700、Key 文件 644，容器 UID 10001 正常读取凭证文件。
- `/readyz`、未鉴权请求返回 401、已鉴权读取账户状态均符合预期。
- 移除并重新创建容器后，Redis 中的验证数据仍存在。
- 验证过程未调用真实 Scamalytics；临时项目及其测试数据卷已清理。


## 提交前验证

运行 Ruff 检查、格式检查、lock 一致性检查和全部 35 项测试，均通过。服务令牌必须显式配置，源码不再提供硬编码默认值；新增回归测试验证缺少令牌时拒绝启动。待提交文件及部署压缩包内容已检查，未包含本地运行凭证。
