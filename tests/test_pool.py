import asyncio
from datetime import UTC, datetime

import pytest

from netools.config import KeyConfig
from netools.models import ServiceError
from netools.upstream import Credits


async def test_atomic_budget_across_pool_instances(harness_factory, redis):
    key = KeyConfig(
        id="one",
        username="one",
        key="synthetic",
        max_concurrency=1024,
        requests_per_second=10000,
    )
    first = harness_factory(keys=[key])
    second = harness_factory(keys=[key], mock=first.mock)
    first.mock.remaining[key.username] = 100
    await first.pool._probe(key)

    reservations = await asyncio.gather(
        *[(first.pool if i % 2 else second.pool).try_reserve(key) for i in range(300)]
    )
    accepted = [lease for status, lease in reservations if status == "ok"]
    assert len(accepted) == 100
    assert await redis.hget(first.pool.state_keys(key)[0], "remaining") == "0"
    await asyncio.gather(
        *[first.pool.finish(lease, credits=Credits(remaining=100)) for lease in accepted]
    )
    assert await redis.hget(first.pool.state_keys(key)[0], "remaining") == "0"
    assert await redis.zcard(first.pool.state_keys(key)[1]) == 0


async def test_shared_concurrency_limit(harness_factory, redis):
    key = KeyConfig(
        id="one",
        username="one",
        key="synthetic",
        max_concurrency=3,
        requests_per_second=10000,
    )
    first = harness_factory(keys=[key])
    second = harness_factory(keys=[key], mock=first.mock)
    await first.pool._probe(key)
    reservations = await asyncio.gather(
        *[(first.pool if i % 2 else second.pool).try_reserve(key) for i in range(80)]
    )
    accepted = [lease for status, lease in reservations if status == "ok"]
    assert len(accepted) == 3
    assert {status for status, _ in reservations} == {"ok", "busy"}
    await first.pool.finish(accepted[0])
    assert (await second.pool.try_reserve(key))[0] == "ok"
    assert await redis.zcard(first.pool.state_keys(key)[1]) == 3


async def test_shared_token_bucket(harness_factory, redis):
    key = KeyConfig(id="one", username="one", key="synthetic", requests_per_second=2)
    harness = harness_factory(keys=[key])
    await harness.pool._probe(key)
    await redis.hset(harness.pool.state_keys(key)[0], "tokens", "2")
    reservations = await asyncio.gather(*[harness.pool.try_reserve(key) for _ in range(30)])
    assert sum(status == "ok" for status, _ in reservations) == 2


async def test_concurrent_probes_are_coalesced(harness_factory):
    first = harness_factory()
    second = harness_factory(keys=first.keys, mock=first.mock)
    first.mock.delay = 0.05
    await asyncio.gather(
        *[(first.pool if i % 2 else second.pool)._probe(first.keys[0]) for i in range(100)]
    )
    assert len(first.mock.calls) == 1
    assert first.mock.calls[0][1] is True


async def test_probe_budget_and_fail_closed(harness_factory, settings, redis):
    config = settings.model_copy(update={"max_probes_per_month": 1})
    harness = harness_factory(config=config)
    key = harness.keys[0]
    harness.mock.failures[key.username] = "probe"
    await harness.pool._probe(key)
    await redis.hset(harness.pool.state_keys(key)[0], "blocked_until", "0")
    status, _ = await harness.pool.try_reserve(key, probe=True)
    assert status == "probe_limit"
    assert (await harness.pool.try_reserve(key))[0] == "needs_probe"
    assert len(harness.mock.calls) == 1
    with pytest.raises(ServiceError):
        await harness.service.lookup("8.8.8.8")
    assert all(test for _, test, _ in harness.mock.calls)


async def test_safety_margin_prevents_last_credits(harness_factory, settings):
    harness = harness_factory(config=settings.model_copy(update={"quota_safety_margin": 5}))
    key = harness.keys[0]
    harness.mock.remaining[key.username] = 6
    await harness.pool._probe(key)
    status, lease = await harness.pool.try_reserve(key)
    assert status == "ok"
    await harness.pool.finish(lease)
    assert (await harness.pool.try_reserve(key))[0] == "exhausted"


async def test_stale_response_and_topup_do_not_replenish(harness_factory, redis):
    harness = harness_factory()
    key = harness.keys[0]
    await harness.pool._probe(key)
    _, lease = await harness.pool.try_reserve(key)
    await harness.pool.finish(lease, credits=Credits(remaining=10))
    assert await redis.hget(lease.state, "remaining") == "10"
    await redis.hset(lease.state, "next_probe_at", "0")
    harness.mock.remaining[key.username] = 10000
    await harness.pool._probe(key)
    assert await redis.hget(lease.state, "remaining") == "10"


async def test_expired_leases_reclaimed_without_refunding(harness_factory, redis):
    key = KeyConfig(id="one", username="one", key="synthetic", max_concurrency=1)
    harness = harness_factory(keys=[key])
    await harness.pool._probe(key)
    _, lease = await harness.pool.try_reserve(key)
    await redis.zadd(lease.leases, {lease.token: 1})
    status, _ = await harness.pool.try_reserve(key)
    assert status == "ok"
    assert await redis.hget(lease.state, "remaining") == "998"


async def test_late_success_does_not_clear_cooldown(harness_factory, redis):
    from netools.upstream import UpstreamError

    harness = harness_factory()
    key = harness.keys[0]
    await harness.pool._probe(key)
    _, older = await harness.pool.try_reserve(key)
    _, newer = await harness.pool.try_reserve(key)
    await harness.pool.finish(newer, error=UpstreamError("rate_limited", 120))
    blocked = await redis.hget(older.state, "blocked_until")
    await harness.pool.finish(older, credits=Credits(remaining=999))
    assert await redis.hget(older.state, "blocked_until") == blocked
    assert (await harness.pool.try_reserve(key))[0] == "cooldown"


async def test_month_rollover_rechecks_budget_but_shares_leases(
    harness_factory, monkeypatch, redis
):
    class Clock(datetime):
        current = datetime(2026, 1, 31, 23, 59, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr("netools.pool.datetime", Clock)
    key = KeyConfig(
        id="one",
        username="one",
        key="synthetic",
        max_concurrency=2,
        requests_per_second=10000,
    )
    harness = harness_factory(keys=[key])
    await harness.pool._probe(key)
    _, old_lease = await harness.pool.try_reserve(key)
    Clock.current = datetime(2026, 2, 1, tzinfo=UTC)
    new_state, leases, _ = harness.pool.state_keys(key)
    assert new_state != old_lease.state
    assert leases == old_lease.leases
    assert (await harness.pool.try_reserve(key))[0] == "needs_probe"
    await harness.pool._probe(key)
    assert await redis.hget(new_state, "probes") == "1"
    assert await redis.hget(new_state, "remaining") == "999"
    _, current_lease = await harness.pool.try_reserve(key)
    assert (await harness.pool.try_reserve(key))[0] == "busy"
    await harness.pool.finish(old_lease)
    await harness.pool.finish(current_lease)
    assert await redis.zcard(leases) == 0
