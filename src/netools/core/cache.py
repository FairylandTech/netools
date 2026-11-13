import asyncio
import hashlib
import random
from collections.abc import Awaitable, Callable
from uuid import uuid4

from pydantic import BaseModel
from redis.asyncio import Redis

from netools.core.config import Settings
from netools.core.scripts import read_lua


class SingleFlightCache[T: BaseModel]:
    """Redis result cache with a per-IP lock so concurrent misses fetch once.

    Results are stored as JSON under ``<cache_prefix>:<sha256(ip)>``; the lock lives
    under ``<lock_prefix>:<sha256(ip)>`` and is released by token comparison, so a
    late request can never remove a newer holder's lock. Cached results are
    returned with ``cached=True``.
    """

    def __init__(
        self,
        redis: Redis,
        settings: Settings,
        model: type[T],
        *,
        cache_prefix: str,
        lock_prefix: str,
    ):
        self.redis = redis
        self.settings = settings
        self.model = model
        self.cache_prefix = cache_prefix
        self.lock_prefix = lock_prefix
        self._unlock = redis.register_script(read_lua(__file__, "unlock.lua"))

    def keys(self, ip: str) -> tuple[str, str]:
        digest = hashlib.sha256(ip.encode()).hexdigest()
        return f"{self.cache_prefix}:{digest}", f"{self.lock_prefix}:{digest}"

    async def _cached(self, cache_key: str) -> T | None:
        cached: str | bytes | None = await self.redis.get(cache_key)
        if cached is None:
            return None
        return self.model.model_validate_json(cached).model_copy(update={"cached": True})

    async def get_or_fetch(self, ip: str, fetch: Callable[[], Awaitable[T]]) -> T:
        cache_key, lock_key = self.keys(ip)
        token = uuid4().hex
        while True:
            result = await self._cached(cache_key)
            if result is not None:
                return result
            locked = await self.redis.set(
                lock_key, token, nx=True, px=self.settings.lease_seconds * 1000
            )
            if locked:
                break
            await asyncio.sleep(random.uniform(0.02, 0.06))
        try:
            # Another worker may have filled the cache between GET and SET NX.
            result = await self._cached(cache_key)
            if result is not None:
                return result
            result = await fetch()
            await self.redis.set(
                cache_key, result.model_dump_json(), ex=self.settings.cache_ttl_seconds
            )
            return result
        finally:
            await self._unlock(keys=[lock_key], args=[token])
