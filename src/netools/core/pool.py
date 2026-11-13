import asyncio
import random
import time
from typing import Protocol

from redis.asyncio import Redis

from netools.core.config import Settings
from netools.core.errors import ServiceError


class PoolKey(Protocol):
    id: str


class KeyPool[K: PoolKey, L]:
    """Round-robin account selection on top of an atomic Redis reservation.

    Subclasses implement ``try_reserve`` (one Lua call) and ``state_keys``; the
    reservation status decides admission, this class only decides which account to
    try next and how long to wait for a pending one.
    """

    pending_statuses: frozenset[str] = frozenset({"busy"})
    unavailable_code = "no_key_available"
    unavailable_message = "No API key is currently available"

    def __init__(self, redis: Redis, keys: list[K], settings: Settings):
        self.redis = redis
        self.keys = keys
        self.settings = settings
        self._cursor = random.randrange(len(keys)) if keys else 0

    def state_keys(self, key: K) -> tuple[str, ...]:
        raise NotImplementedError

    async def try_reserve(self, key: K) -> tuple[str, L]:
        raise NotImplementedError

    async def _recover(self, key: K, status: str) -> tuple[str, L] | None:
        """Hook for statuses that can be resolved immediately (e.g. a quota probe)."""
        return None

    async def acquire(self, *, excluded: set[str] | None = None) -> L:
        excluded = excluded or set()
        deadline = time.monotonic() + self.settings.key_wait_seconds
        while self.keys:
            pending = False
            start = self._cursor
            self._cursor = (self._cursor + 1) % len(self.keys)
            for offset in range(len(self.keys)):
                key = self.keys[(start + offset) % len(self.keys)]
                if key.id in excluded:
                    continue
                status, lease = await self.try_reserve(key)
                if status == "ok":
                    return lease
                recovered = await self._recover(key, status)
                if recovered is not None:
                    status, lease = recovered
                    if status == "ok":
                        return lease
                pending |= status in self.pending_statuses
            if not pending or time.monotonic() >= deadline:
                break
            await asyncio.sleep(random.uniform(0.02, 0.06))
        raise ServiceError(self.unavailable_code, self.unavailable_message)

    async def _states(self) -> list[dict]:
        pipeline = self.redis.pipeline(transaction=False)
        for key in self.keys:
            pipeline.hgetall(self.state_keys(key)[0])
        return await pipeline.execute()
