import asyncio
import os
from collections import Counter
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from netools.config import KeyConfig, Settings
from netools.pool import KeyPool
from netools.service import ScoreService
from netools.upstream import ScamalyticsClient


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        service_api_token="synthetic-service-token-for-tests",
        scamalytics_keys_file=tmp_path / "keys.json",
        redis_url=os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:16379/15"),
        redis_prefix=f"test_{uuid4().hex}",
        quota_safety_margin=0,
        key_wait_seconds=0.5,
    )


@pytest.fixture
async def redis(settings):
    connection = Redis.from_url(
        settings.redis_url.get_secret_value(), decode_responses=True, socket_connect_timeout=2
    )
    try:
        await connection.ping()
    except RedisError:
        await connection.aclose()
        pytest.fail("Start the dedicated test Redis; see .codex/operations.md")
    yield connection
    async for key in connection.scan_iter(match=f"{settings.redis_prefix}:*"):
        await connection.delete(key)
    await connection.aclose()


class MockScamalytics:
    def __init__(self, keys: list[KeyConfig]):
        self.remaining = dict.fromkeys((key.username for key in keys), 1000)
        self.failures: dict[str, str] = {}
        self.calls: list[tuple[str, bool, str]] = []
        self.active = Counter()
        self.peak = Counter()
        self.delay = 0.005
        self.live_started = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        username = request.url.path.rsplit("/", 1)[-1]
        test = request.url.params.get("test") == "1"
        ip = request.url.params["ip"]
        self.calls.append((username, test, ip))
        self.active[username] += 1
        self.peak[username] = max(self.peak[username], self.active[username])
        try:
            if not test:
                self.live_started.set()
            await asyncio.sleep(self.delay)
            failure = self.failures.get(username)
            if failure == "probe" and test:
                return httpx.Response(500)
            if failure == "authentication" and not test:
                return httpx.Response(401, text=f"secret: {request.url.params['key']}")
            if failure == "rate_limited" and not test:
                return httpx.Response(429, headers={"Retry-After": "120"})
            if (failure == "exhausted" and not test) or self.remaining[username] <= 0:
                return httpx.Response(
                    200,
                    json={
                        "scamalytics": {
                            "status": "error",
                            "error": "Excess limit: credits exhausted",
                        }
                    },
                )
            if not test:
                self.remaining[username] -= 1
            return httpx.Response(
                200,
                json={
                    "scamalytics": {
                        "status": "ok",
                        "mode": "test" if test else "live",
                        "ip": ip,
                        "scamalytics_score": 23,
                        "scamalytics_risk": "medium",
                        "credits": {"remaining": self.remaining[username]},
                    }
                },
            )
        finally:
            self.active[username] -= 1


@dataclass
class Harness:
    service: ScoreService
    pool: KeyPool
    client: ScamalyticsClient
    mock: MockScamalytics
    keys: list[KeyConfig]


@pytest.fixture
async def harness_factory(redis, settings):
    clients = []

    def factory(*, keys=None, mock=None, config=None):
        config = config or settings
        keys = keys or [
            KeyConfig(
                id="key-a",
                username="account_a",
                key="synthetic-key-a",
                requests_per_second=10000,
                max_concurrency=32,
            )
        ]
        mock = mock or MockScamalytics(keys)
        http = httpx.AsyncClient(transport=httpx.MockTransport(mock))
        clients.append(http)
        client = ScamalyticsClient(http, config.upstream_timeout_seconds)
        pool = KeyPool(redis, keys, config, client)
        return Harness(ScoreService(redis, pool, client, config), pool, client, mock, keys)

    yield factory
    for http in clients:
        await http.aclose()
