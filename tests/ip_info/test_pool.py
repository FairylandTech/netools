"""Requires a dedicated Redis; these tests execute the production Lua scripts."""

import asyncio

from netools.ip_info.client import IpregistryError
from tests.support import registry_key


async def test_atomic_budget_across_instances_and_stale_responses(registry_factory, redis):
    key = registry_key(max_concurrency=1024).model_copy(update={"initial_credits": 100})
    first, second = registry_factory(keys=[key]), registry_factory(keys=[key])
    reservations = await asyncio.gather(
        *[(first.pool if i % 2 else second.pool).try_reserve(key) for i in range(300)]
    )
    accepted = [lease for status, lease in reservations if status == "ok"]
    assert len(accepted) == 100
    assert await redis.hget(accepted[0].state, "remaining") == "0"
    await asyncio.gather(*[first.pool.finish(lease, remaining=1000) for lease in accepted])
    assert await redis.hget(accepted[0].state, "remaining") == "0"
    assert await redis.zcard(accepted[0].leases) == 0
    assert await redis.ttl(accepted[0].state) == -1


async def test_shared_concurrency_and_expired_lease_no_refund(registry_factory, redis):
    key = registry_key(max_concurrency=3)
    first, second = registry_factory(keys=[key]), registry_factory(keys=[key])
    reservations = await asyncio.gather(
        *[(first.pool if i % 2 else second.pool).try_reserve(key) for i in range(80)]
    )
    accepted = [lease for status, lease in reservations if status == "ok"]
    assert len(accepted) == 3
    assert {status for status, _ in reservations} == {"ok", "busy"}
    await redis.zadd(accepted[0].leases, {accepted[0].token: 1})
    assert (await second.pool.try_reserve(key))[0] == "ok"
    assert await redis.hget(accepted[0].state, "remaining") == "996"
    await first.pool.finish(accepted[0])
    assert await redis.zcard(accepted[0].leases) == 3


async def test_shared_rate_limit_and_fractional_rate(registry_factory):
    key = registry_key().model_copy(update={"requests_per_second": 2})
    first, second = registry_factory(keys=[key]), registry_factory(keys=[key])
    results = await asyncio.gather(
        *[(first.pool if i % 2 else second.pool).try_reserve(key) for i in range(30)]
    )
    assert sum(status == "ok" for status, _ in results) == 2
    slow = registry_key("slow").model_copy(update={"requests_per_second": 0.1})
    pool = registry_factory(keys=[slow]).pool
    status, lease = await pool.try_reserve(slow)
    assert status == "ok"
    await pool.finish(lease)
    assert (await pool.try_reserve(slow))[0] == "busy"


async def test_rotation_and_config_increase_never_reinitialize_budget(registry_factory, redis):
    key = registry_key()
    first = registry_factory(keys=[key])
    _, lease = await first.pool.try_reserve(key)
    await first.pool.finish(lease, remaining=10)
    rotated = key.model_copy(update={"id": "new-alias", "initial_credits": 10000})
    second = registry_factory(keys=[rotated])
    assert first.pool.state_keys(key) == second.pool.state_keys(rotated)
    status, current = await second.pool.try_reserve(rotated)
    assert status == "ok"
    assert await redis.hget(current.state, "remaining") == "9"
    await second.pool.finish(current, remaining=20000)
    assert await redis.hget(current.state, "remaining") == "9"


async def test_out_of_order_success_retains_newer_cooldown(registry_factory, redis):
    h = registry_factory()
    _, older = await h.pool.try_reserve(h.keys[0])
    _, newer = await h.pool.try_reserve(h.keys[0])
    await h.pool.finish(newer, error=IpregistryError("rate_limited", 120))
    blocked = await redis.hget(older.state, "blocked_until")
    await h.pool.finish(older, remaining=999)
    assert await redis.hget(older.state, "remaining") == "998"
    assert await redis.hget(older.state, "blocked_until") == blocked
    assert (await h.pool.try_reserve(h.keys[0]))[0] == "cooldown"
