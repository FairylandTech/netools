# IP 风险评分服务（netools）API 调用实例文档

当前源码增加了 `ipregistry` 和 `ipregistry_error` 字段；部署更新后可以同时读取注册信息。配置及响应结构见 [ipregistry 接入说明](../.codex/docs/ipregistry.md)。下文风险字段继续兼容旧版。

## 基本信息

- **服务部署地址（Base URL）**：`https://iprisk.service.kylin.nuvo.eu.cc`
- **在线交互文档（Swagger UI）**：`https://iprisk.service.kylin.nuvo.eu.cc/docs`
- **OpenAPI 规范**：`https://iprisk.service.kylin.nuvo.eu.cc/openapi.json`
- **数据格式**：请求与响应均采用 UTF-8 JSON

---

## 认证方式

除健康检查端点（`/healthz`、`/readyz`）和文档页面（`/docs`）外，所有业务接口均需在 HTTP 请求头中携带 Bearer Token：

```http
Authorization: Bearer <SERVICE_API_TOKEN>
```

> 请将本文档示例中的 `<SERVICE_API_TOKEN>` 替换为服务端 `.env` 中配置的实际 `SERVICE_API_TOKEN`。若在浏览器打开 `/docs` 测试，点击右上角 **Authorize** 按钮后直接填入令牌值即可（无需手动输入 `Bearer ` 前缀）。

---

## 接口列表与调用示例

### 1. 健康与就绪检查（免认证）

用于探活及检查后端 Redis 状态连接是否正常。不会触发上游 Scamalytics 请求，也不消耗额度。

#### 请求示例（cURL）

```bash
# 进程存活检查
curl --fail https://iprisk.service.kylin.nuvo.eu.cc/healthz

# 服务与 Redis 就绪检查
curl --fail https://iprisk.service.kylin.nuvo.eu.cc/readyz
```

#### 响应示例（`200 OK`）

```json
{"status": "ok"}
```

```json
{"status": "ready"}
```

---

### 2. 单 IP 风险评分查询

- **请求方法与路径**：`GET /v1/score`
- **查询参数**：
  - `ip`（必填）：待查询的 IPv4 或 IPv6 地址。IPv6 地址会自动规范化以便缓存去重；不支持域名、CIDR 网段或带 `%` 作用域标识（scope id）的 IPv6。

#### 请求示例（cURL）

```bash
# 查询 IPv4
curl --fail-with-body \
  -H 'Authorization: Bearer <SERVICE_API_TOKEN>' \
  'https://iprisk.service.kylin.nuvo.eu.cc/v1/score?ip=8.8.8.8'

# 查询 IPv6（URL 中冒号可直接传递或按需 URL 编码）
curl --fail-with-body \
  -G \
  -H 'Authorization: Bearer <SERVICE_API_TOKEN>' \
  --data-urlencode 'ip=2606:4700:4700::1111' \
  'https://iprisk.service.kylin.nuvo.eu.cc/v1/score'
```

#### 成功响应示例（`200 OK`）

```json
{
  "ip": "8.8.8.8",
  "score": 0,
  "risk": "low",
  "key_id": "account-1",
  "checked_at": "2026-11-10T03:52:45Z",
  "cached": false
}
```

#### 响应字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ip` | `string` | 规范化后的 IP 地址 |
| `score` | `integer` | Scamalytics 欺诈风险评分，范围 `0`–`100`（分值越高风险越大） |
| `risk` | `string` | 风险等级枚举：`low`、`medium`、`high`、`very high` |
| `key_id` | `string` | 本次查询命中的本地账户池别名（不暴露真实上游凭证） |
| `checked_at` | `string` | 上游查询完成时间（ISO 8601 UTC 时间戳）；命中缓存时保留首次查询时间 |
| `cached` | `boolean` | `true` 表示本次结果直接命中 Redis 缓存（未消耗上游额度），`false` 表示实时查询 |

---

### 3. 批量 IP 风险评分查询

- **请求方法与路径**：`POST /v1/scores`
- **请求头**：`Content-Type: application/json`
- **请求体**：
  - `ips`（必填）：包含 `1` 至 `1000` 个 IPv4 / IPv6 字符串的数组。
- **行为说明**：
  - 请求开始前先校验全部 IP 格式；若任意一项格式非法、数组为空或超过 1000 项，直接返回 `422 Unprocessable Entity`，不会发起任何上游查询。
  - 校验通过后按输入顺序（保留重复项）返回结果列表，状态码为 `200 OK`。单个 IP 在查询期间发生的运行时错误会记录在对应元素的 `error` 字段中，不会中断整批响应。
  - 批量请求总耗时可能超过单次查询超时时间，建议客户端将请求超时时间设置得比单次查询更充裕（如 30–60 秒）。

#### 请求示例（cURL）

```bash
curl --fail-with-body -X POST \
  -H 'Authorization: Bearer <SERVICE_API_TOKEN>' \
  -H 'Content-Type: application/json' \
  -d '{
    "ips": [
      "8.8.8.8",
      "1.1.1.1",
      "2606:4700:4700::1111"
    ]
  }' \
  'https://iprisk.service.kylin.nuvo.eu.cc/v1/scores'
```

#### 响应示例（`200 OK`，含全部成功或部分失败情况）

```json
{
  "results": [
    {
      "ip": "8.8.8.8",
      "result": {
        "ip": "8.8.8.8",
        "score": 0,
        "risk": "low",
        "key_id": "account-1",
        "checked_at": "2026-11-10T03:52:45Z",
        "cached": false
      },
      "error": null
    },
    {
      "ip": "1.1.1.1",
      "result": {
        "ip": "1.1.1.1",
        "score": 2,
        "risk": "low",
        "key_id": "account-1",
        "checked_at": "2026-11-10T03:50:12Z",
        "cached": true
      },
      "error": null
    },
    {
      "ip": "2606:4700:4700::1111",
      "result": null,
      "error": {
        "code": "no_key_available",
        "message": "No API key is currently available"
      }
    }
  ]
}
```

---

### 4. 账户池与额度状态查询

- **请求方法与路径**：`GET /v1/keys`
- **说明**：仅读取 Redis 中的本地状态快照，不会触发上游测试预检请求，也不会暴露真实 `username` 或 `key`。

#### 请求示例（cURL）

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer <SERVICE_API_TOKEN>' \
  'https://iprisk.service.kylin.nuvo.eu.cc/v1/keys'
```

#### 响应示例（`200 OK`）

```json
{
  "keys": [
    {
      "id": "account-1",
      "remaining_estimate": 4980,
      "reported_remaining": 4982,
      "safety_margin": 5,
      "probes_this_month": 1,
      "observed_at_unix_ms": 1794282765000,
      "blocked_until_unix_ms": null,
      "last_error": null
    }
  ]
}
```

#### 响应字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `string` | 账户本地别名 |
| `remaining_estimate` | `integer \| null` | 本地保守估计的剩余可用额度（已扣除在途预留与失败请求，首次预检前为 `null`） |
| `reported_remaining` | `integer \| null` | 上游最后一次返回的近似剩余额度（首次预检前为 `null`） |
| `safety_margin` | `integer` | 安全保留额度阈值，当 `remaining_estimate <= safety_margin` 时停止使用该账户发起正式查询 |
| `probes_this_month` | `integer` | 当前 UTC 自然月内已执行的 `test=1` 额度预检次数 |
| `observed_at_unix_ms` | `integer \| null` | 最近一次更新额度快照的 Unix 毫秒时间戳 |
| `blocked_until_unix_ms` | `integer \| null` | 账户因限流或故障进入冷却期的截止时间戳（Unix 毫秒），未冷却时为 `null` |
| `last_error` | `string \| null` | 最近一次上游错误类别（如 `auth`、`rate_limit`、`quota`、`upstream` 等） |

---

## 错误响应与状态码

业务错误（非 422 参数校验错误）统一采用如下 JSON 结构：

```json
{
  "error": {
    "code": "no_key_available",
    "message": "No API key is currently available"
  }
}
```

| HTTP 状态码 | `error.code` | 触发原因 | 调用方处理建议 |
| --- | --- | --- | --- |
| `401` | `unauthorized` | 缺少 `Authorization` 头、格式非 `Bearer` 或令牌不匹配 | 检查 `SERVICE_API_TOKEN` 配置 |
| `422` | *(FastAPI 校验格式)* | IP 格式非法、缺少必填参数、批量数组为空或超过 1000 项 | 修正请求参数后再发送，不要原样重试 |
| `502` | `upstream_unavailable` | 上游正式查询失败，且尝试切换池内其他账户后仍未成功 | 做有界指数退避重试 |
| `503` | `no_key_available` | 所有账户额度耗尽、处于错误冷却、并发/速率受限或预检失败 | 响应含 `Retry-After: 1`；建议退避重试并检查 `/v1/keys` 余额 |
| `503` | `overloaded` | 服务单进程在途等待查询数已达上限（默认 512） | 降低客户端并发，按 `Retry-After` 退避 |
| `503` | `state_unavailable` | 后端 Redis 状态存储暂时不可达 | 等待服务恢复后重试 |
| `504` | `lookup_timeout` | 单次查询总耗时超过上限（默认 15 秒） | 按需退避重试 |

---

## 常用编程语言调用示例

### Python（异步 `httpx`）

```python
import asyncio
import os
import httpx

BASE_URL = "https://iprisk.service.kylin.nuvo.eu.cc"
API_TOKEN = os.environ["SERVICE_API_TOKEN"]


async def main() -> None:
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    timeout = httpx.Timeout(30.0, connect=5.0)

    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=timeout) as client:
        # 1. 单 IP 查询
        resp = await client.get("/v1/score", params={"ip": "8.8.8.8"})
        resp.raise_for_status()
        score_data = resp.json()
        print(
            f"单 IP 结果: ip={score_data['ip']}, score={score_data['score']}, risk={score_data['risk']}"
        )

        if score_data.get("ipregistry") is not None:
            print("IP 注册信息:", score_data["ipregistry"]["data"])
        elif score_data.get("ipregistry_error") is not None:
            print("IP 注册信息查询错误:", score_data["ipregistry_error"])

        # 2. 批量 IP 查询
        batch_resp = await client.post(
            "/v1/scores",
            json={"ips": ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"]},
        )
        batch_resp.raise_for_status()
        for item in batch_resp.json()["results"]:
            if item["error"] is None:
                res = item["result"]
                print(
                    f"[成功] {item['ip']} -> score={res['score']}, risk={res['risk']}, cached={res['cached']}"
                )
            else:
                err = item["error"]
                print(f"[失败] {item['ip']} -> code={err['code']}, message={err['message']}")

        # 3. 查看 Key 池状态
        keys_resp = await client.get("/v1/keys")
        keys_resp.raise_for_status()
        print("Key 池状态:", keys_resp.json())


if __name__ == "__main__":
    asyncio.run(main())
```

### JavaScript / TypeScript（Node.js 18+ / 浏览器 `fetch`）

```javascript
const BASE_URL = "https://iprisk.service.kylin.nuvo.eu.cc";
const API_TOKEN = process.env.SERVICE_API_TOKEN;

const headers = {
  Authorization: `Bearer ${API_TOKEN}`,
  "Content-Type": "application/json",
};

async function querySingleIp(ip) {
  const url = new URL("/v1/score", BASE_URL);
  url.searchParams.set("ip", ip);

  const response = await fetch(url, { headers });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(`API error (${response.status}): ${data?.error?.code} - ${data?.error?.message}`);
  }
  return data;
}

async function queryBatchIps(ips) {
  const response = await fetch(`${BASE_URL}/v1/scores`, {
    method: "POST",
    headers,
    body: JSON.stringify({ ips }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(`Batch request failed (${response.status}): ${JSON.stringify(data)}`);
  }
  return data.results;
}

// 调用示例
(async () => {
  const single = await querySingleIp("8.8.8.8");
  console.log("Single score:", single);

  const batch = await queryBatchIps(["8.8.8.8", "1.1.1.1"]);
  console.log("Batch results:", batch);
})();
```

### Go（标准库 `net/http`）

```go
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"time"
)

const baseURL = "https://iprisk.service.kylin.nuvo.eu.cc"

func main() {
	token := os.Getenv("SERVICE_API_TOKEN")
	client := &http.Client{Timeout: 20 * time.Second}

	// 1. 单 IP 查询
	u, _ := url.Parse(baseURL + "/v1/score")
	q := u.Query()
	q.Set("ip", "8.8.8.8")
	u.RawQuery = q.Encode()

	req, _ := http.NewRequest(http.MethodGet, u.String(), nil)
	req.Header.Set("Authorization", "Bearer "+token)

	resp, err := client.Do(req)
	if err != nil {
		panic(err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	fmt.Printf("GET /v1/score (%d): %s\n", resp.StatusCode, string(body))

	// 2. 批量查询
	payload, _ := json.Marshal(map[string][]string{
		"ips": {"8.8.8.8", "1.1.1.1"},
	})
	batchReq, _ := http.NewRequest(http.MethodPost, baseURL+"/v1/scores", bytes.NewReader(payload))
	batchReq.Header.Set("Authorization", "Bearer "+token)
	batchReq.Header.Set("Content-Type", "application/json")

	batchResp, err := client.Do(batchReq)
	if err != nil {
		panic(err)
	}
	defer batchResp.Body.Close()
	batchBody, _ := io.ReadAll(batchResp.Body)
	fmt.Printf("POST /v1/scores (%d): %s\n", batchResp.StatusCode, string(batchBody))
}
```
