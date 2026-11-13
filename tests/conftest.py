import asyncio
import json
import os
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from netools.app import create_app
from netools.core.config import Settings
from netools.ip_info.client import IpregistryClient
from netools.ip_info.pool import IpregistryKeyPool
from netools.ip_info.service import InfoService
from netools.ip_risk.client import ScamalyticsClient
from netools.ip_risk.config import ScamalyticsKeyConfig
from netools.ip_risk.pool import ScamalyticsKeyPool
from netools.ip_risk.service import RiskService
from netools.lookup.service import LookupService
from tests.support import (
    MockScamalytics,
    registry_key,
    registry_response,
    scamalytics_live_response,
)


def pytest_collection_modifyitems(items):
    # Mark by fixture dependency, including transitive harness dependencies.
    for item in items:
        if "redis" in item.fixturenames:
            item.add_marker(pytest.mark.redis)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        service_api_token="synthetic-service-token-for-tests",
        scamalytics_keys_file=tmp_path / "keys.json",
        redis_url=os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:16379/15"),
        redis_prefix=f"test_{uuid4().hex}",
        quota_safety_margin=0,
        ipregistry_quota_safety_margin=0,
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
        pytest.fail("Connect a dedicated test Redis; see .claude/docs/testing.md")
    yield connection
    async for key in connection.scan_iter(match=f"{settings.redis_prefix}:*"):
        await connection.delete(key)
    await connection.aclose()


@dataclass
class RiskHarness:
    """Scamalytics stack on a real Redis, wrapped by a LookupService without ipregistry."""

    lookup: LookupService
    service: RiskService
    pool: ScamalyticsKeyPool
    client: ScamalyticsClient
    mock: MockScamalytics
    keys: list[ScamalyticsKeyConfig]


@pytest.fixture
async def harness_factory(redis, settings):
    clients = []

    def factory(*, keys=None, mock=None, config=None):
        config = config or settings
        keys = keys or [
            ScamalyticsKeyConfig(
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
        pool = ScamalyticsKeyPool(redis, keys, config, client)
        service = RiskService(redis, pool, client, config)
        return RiskHarness(LookupService(config, service), service, pool, client, mock, keys)

    yield factory
    for http in clients:
        await http.aclose()


@dataclass
class RegistryHarness:
    """ipregistry stack on a real Redis."""

    pool: IpregistryKeyPool
    service: InfoService
    calls: list
    keys: list


@pytest.fixture
async def registry_factory(redis, settings):
    clients = []

    def factory(*, keys=None, respond=None, config=None):
        keys = keys or [registry_key()]
        config = config or settings
        calls = []

        async def handle(request):
            calls.append(request)
            if respond:
                return await respond(request)
            return registry_response(request.url.path[1:])

        http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        clients.append(http)
        pool = IpregistryKeyPool(redis, keys, config)
        service = InfoService(redis, pool, IpregistryClient(http, 1), config)
        return RegistryHarness(pool, service, calls, keys)

    yield factory
    for client in clients:
        await client.aclose()


@pytest.fixture
def stub_state():
    """Cache/lock test double; Lua admission is a stub, NOT a quota/atomicity test."""
    state = MagicMock(spec=Redis)
    cache = {}

    async def get(key):
        return cache.get(key)

    async def put(key, value, nx=False, **kwargs):
        if nx and key in cache:
            return False
        cache[key] = value
        return True

    async def unlock(*, keys, args):
        if cache.get(keys[0]) == args[0]:
            cache.pop(keys[0])

    state.get = AsyncMock(side_effect=get)
    state.set = AsyncMock(side_effect=put)
    state.ping = AsyncMock(return_value=True)
    state.aclose = AsyncMock()
    state.reserve = AsyncMock(return_value="ok")
    state.finish = AsyncMock(return_value=1)
    state.unlock = AsyncMock(side_effect=unlock)

    def register(script):
        if "HINCRBY" in script:
            return state.reserve
        if "ZREM" in script:
            return state.finish
        return state.unlock

    state.register_script.side_effect = register
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=[{}])
    state.pipeline.return_value = pipeline
    state.cache = cache
    return state


@pytest.fixture
async def api_harness(settings, tmp_path, stub_state, monkeypatch):
    """Full ASGI app with both providers configured, Redis stubbed, HTTP mocked."""
    settings.scamalytics_keys_file.write_text(
        json.dumps([{"id": "scam-one", "username": "synthetic-user", "key": "synthetic-scam-key"}])
    )
    settings.ipregistry_keys_file = tmp_path / "registry.json"
    settings.ipregistry_keys_file.write_text(
        json.dumps(
            [
                {
                    "id": name,
                    "account_id": f"account-{name}",
                    "key": f"synthetic-{name}",
                    "initial_credits": 1000,
                }
                for name in ("one", "two")
            ]
        )
    )
    control = MagicMock()
    control.calls = []
    control.delay = 0
    control.failure = None
    control.started = asyncio.Event()

    async def respond(request):
        control.calls.append(request)
        if request.url.host == "api.ipregistry.co":
            control.started.set()
            if control.delay:
                await asyncio.sleep(control.delay)
            if control.failure:
                return control.failure(request)
            return registry_response(request.url.path[1:])
        return scamalytics_live_response(request)

    client_type = httpx.AsyncClient
    monkeypatch.setattr("netools.app.Redis.from_url", lambda *args, **kwargs: stub_state)
    created_clients = []

    def shared_client(**kwargs):
        client = client_type(transport=httpx.MockTransport(respond), **kwargs)
        created_clients.append(client)
        return client

    monkeypatch.setattr("netools.app.httpx.AsyncClient", shared_client)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        app.state.info_pool._cursor = 0
        async with client_type(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer " + settings.service_api_token.get_secret_value()},
        ) as api:
            control.app = app
            control.api = api
            control.state = stub_state
            yield control
    assert len(created_clients) == 1
    assert created_clients[0].is_closed
    stub_state.aclose.assert_awaited_once()
