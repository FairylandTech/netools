import asyncio

import pytest

from netools.config import KeyConfig
from netools.models import ServiceError


async def test_same_ip_single_flight_across_instances(harness_factory):
    first = harness_factory()
    second = harness_factory(keys=first.keys, mock=first.mock)
    first.mock.delay = 0.04
    results = await asyncio.gather(
        *[
            (first.service if i % 2 else second.service).lookup("2606:4700:4700:0:0:0:0:1111")
            for i in range(200)
        ]
    )
    assert len(results) == 200
    assert {result.ip for result in results} == {"2606:4700:4700::1111"}
    assert {result.score for result in results} == {23}
    assert sum(not result.cached for result in results) == 1
    assert sum(not test for _, test, _ in first.mock.calls) == 1
    assert sum(test for _, test, _ in first.mock.calls) == 1


async def test_many_distinct_ips_with_bounded_upstream_concurrency(harness_factory, settings):
    key = KeyConfig(
        id="one",
        username="one",
        key="synthetic",
        max_concurrency=4,
        requests_per_second=10000,
    )
    config = settings.model_copy(update={"key_wait_seconds": 5})
    first = harness_factory(keys=[key], config=config)
    second = harness_factory(keys=[key], mock=first.mock, config=config)
    results = await asyncio.gather(
        *[(first.service if i % 2 else second.service).lookup(f"10.0.0.{i}") for i in range(1, 201)]
    )
    assert len(results) == 200
    assert first.mock.peak[key.username] <= 4
    assert sum(not test for _, test, _ in first.mock.calls) == 200


@pytest.mark.parametrize("failure", ["authentication", "exhausted", "rate_limited"])
async def test_key_failover(harness_factory, redis, failure):
    keys = [
        KeyConfig(id=name, username=name, key=f"synthetic-{name}", requests_per_second=10000)
        for name in ("first", "second")
    ]
    harness = harness_factory(keys=keys)
    harness.pool._cursor = 0
    harness.mock.failures["first"] = failure
    result = await harness.service.lookup("8.8.8.8")
    assert result.key_id == "second"
    first_state = await redis.hgetall(harness.pool.state_keys(keys[0])[0])
    assert first_state["last_error"] == failure
    if failure == "exhausted":
        assert first_state["remaining"] == "0"
    assert len([call for call in harness.mock.calls if not call[1]]) == 2


async def test_cancel_releases_locks_and_keeps_reservation(harness_factory, redis, settings):
    harness = harness_factory()
    key = harness.keys[0]
    await harness.pool._probe(key)
    harness.mock.delay = 10
    task = asyncio.create_task(harness.service.lookup("8.8.8.8"))
    await harness.mock.live_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await redis.zcard(harness.pool.state_keys(key)[1]) == 0
    assert await redis.hget(harness.pool.state_keys(key)[0], "remaining") == "999"
    assert [key async for key in redis.scan_iter(f"{settings.redis_prefix}:lookup:*")] == []
    assert harness.service._active == 0


async def test_total_deadline_releases_resources(harness_factory, settings, redis):
    config = settings.model_copy(update={"lookup_timeout_seconds": 0.05})
    harness = harness_factory(config=config)
    await harness.pool._probe(harness.keys[0])
    harness.mock.delay = 1
    with pytest.raises(ServiceError, match="deadline") as raised:
        await harness.service.lookup("8.8.8.8")
    assert raised.value.status_code == 504
    assert await redis.zcard(harness.pool.state_keys(harness.keys[0])[1]) == 0


async def test_batch_order_duplicates_and_partial_errors(harness_factory, monkeypatch):
    harness = harness_factory()
    original = harness.service.lookup

    async def lookup(ip):
        if ip == "1.1.1.1":
            raise ServiceError("no_key_available", "No API key is currently available")
        return await original(ip)

    monkeypatch.setattr(harness.service, "lookup", lookup)
    ips = ["8.8.8.8", "1.1.1.1", "8.8.8.8"]
    results = await harness.service.batch(ips)
    assert [item.ip for item in results] == ips
    assert results[0].result.score == 23
    assert results[1].error.code == "no_key_available"
    assert results[2].result.score == 23
    assert sum(not test for _, test, _ in harness.mock.calls) == 1


async def test_admission_control(harness_factory, settings):
    config = settings.model_copy(update={"max_pending_lookups": 1})
    harness = harness_factory(config=config)
    harness.mock.delay = 0.1
    task = asyncio.create_task(harness.service.lookup("8.8.8.8"))
    await harness.mock.live_started.wait()
    with pytest.raises(ServiceError) as raised:
        await harness.service.lookup("1.1.1.1")
    assert raised.value.code == "overloaded"
    await task


async def test_redis_failure_is_per_item_error(harness_factory, monkeypatch):
    from redis.exceptions import ConnectionError

    harness = harness_factory()

    async def fail(*args, **kwargs):
        raise ConnectionError("synthetic failure")

    monkeypatch.setattr(harness.service.redis, "get", fail)
    results = await harness.service.batch(["8.8.8.8", "1.1.1.1"])
    assert all(item.error.code == "state_unavailable" for item in results)
    assert harness.mock.calls == []
