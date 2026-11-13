"""Requires a dedicated Redis: single flight, failover and release paths."""

import asyncio

import httpx
import pytest

from netools.core.errors import ServiceError
from tests.support import registry_key, registry_response


async def test_safety_margin_and_exhaustion_stop_calls(registry_factory, settings):
    key = registry_key().model_copy(update={"initial_credits": 6})
    h = registry_factory(
        keys=[key], config=settings.model_copy(update={"ipregistry_quota_safety_margin": 5})
    )
    status, lease = await h.pool.try_reserve(key)
    assert status == "ok"
    await h.pool.finish(lease)
    assert (await h.pool.try_reserve(key))[0] == "exhausted"
    with pytest.raises(ServiceError, match="No ipregistry"):
        await h.service.lookup("8.8.8.8")
    assert h.calls == []


async def test_same_ip_single_flight_across_instances(registry_factory):
    first, second = registry_factory(), registry_factory()
    results = await asyncio.gather(
        *[
            (first.service if i % 2 else second.service).lookup("2606:4700:4700:0:0:0:0:1111")
            for i in range(100)
        ]
    )
    assert {result.data.ip for result in results} == {"2606:4700:4700::1111"}
    assert sum(not result.cached for result in results) == 1
    assert len(first.calls) + len(second.calls) == 1


@pytest.mark.parametrize("failure", [401, 402, 429, 503])
async def test_failover_and_preserved_reservation(registry_factory, redis, failure):
    async def respond(request):
        if request.headers["Authorization"] == "ApiKey synthetic-one":
            return httpx.Response(failure, headers={"Retry-After": "120"})
        return registry_response(request.url.path[1:])

    keys = [registry_key("one"), registry_key("two")]
    h = registry_factory(keys=keys, respond=respond)
    h.pool._cursor = 0
    result = await h.service.lookup("8.8.8.8")
    assert result.key_id == "two"
    state, leases = h.pool.state_keys(keys[0])
    assert await redis.hget(state, "remaining") == ("0" if failure == 402 else "999")
    assert await redis.zcard(leases) == 0
    assert len(h.calls) == 2


async def test_cancel_releases_without_refund_or_cache(registry_factory, redis, settings):
    started = asyncio.Event()

    async def respond(request):
        started.set()
        await asyncio.Event().wait()

    h = registry_factory(respond=respond)
    task = asyncio.create_task(h.service.lookup("8.8.8.8"))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state, leases = h.pool.state_keys(h.keys[0])
    assert await redis.hget(state, "remaining") == "999"
    assert await redis.zcard(leases) == 0
    assert [k async for k in redis.scan_iter(f"{settings.redis_prefix}:ipregistry:*")] == []
