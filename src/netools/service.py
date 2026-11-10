import asyncio
import hashlib
import random
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from netools.config import Settings
from netools.models import BatchItem, ErrorDetail, Score, ServiceError, normalize_ip
from netools.pool import KeyPool
from netools.upstream import ScamalyticsClient, UpstreamError


class ScoreService:
    def __init__(self, redis: Redis, pool: KeyPool, client: ScamalyticsClient, settings: Settings):
        self.redis = redis
        self.pool = pool
        self.client = client
        self.settings = settings
        self._active = 0
        self._unlock = redis.register_script(
            (Path(__file__).with_name("lua") / "unlock.lua").read_text()
        )

    async def lookup(self, ip: str) -> Score:
        ip = normalize_ip(ip)
        # No await between check and increment: admission is atomic on this event loop.
        if self._active >= self.settings.max_pending_lookups:
            raise ServiceError("overloaded", "The service is busy; retry later")
        self._active += 1
        try:
            async with asyncio.timeout(self.settings.lookup_timeout_seconds):
                return await self._lookup(ip)
        except TimeoutError:
            raise ServiceError("lookup_timeout", "The lookup deadline was exceeded", 504) from None
        except RedisError:
            raise ServiceError("state_unavailable", "Redis is unavailable") from None
        finally:
            self._active -= 1

    async def _lookup(self, ip: str) -> Score:
        digest = hashlib.sha256(ip.encode()).hexdigest()
        cache_key = f"{self.settings.redis_prefix}:score:{digest}"
        lock_key = f"{self.settings.redis_prefix}:lookup:{digest}"
        token = uuid4().hex
        while True:
            cached = await self.redis.get(cache_key)
            if cached is not None:
                return Score.model_validate_json(cached).model_copy(update={"cached": True})
            locked = await self.redis.set(
                lock_key,
                token,
                nx=True,
                px=self.settings.lease_seconds * 1000,
            )
            if locked:
                break
            await asyncio.sleep(random.uniform(0.02, 0.06))
        try:
            # Another worker may have filled the cache between GET and SET NX.
            cached = await self.redis.get(cache_key)
            if cached is not None:
                return Score.model_validate_json(cached).model_copy(update={"cached": True})
            score = await self._fetch(ip)
            await self.redis.set(
                cache_key, score.model_dump_json(), ex=self.settings.cache_ttl_seconds
            )
            return score
        finally:
            await self._unlock(keys=[lock_key], args=[token])

    async def _fetch(self, ip: str) -> Score:
        excluded: set[str] = set()
        for _ in range(self.settings.max_attempts):
            try:
                lease = await self.pool.acquire(excluded=excluded)
            except ServiceError:
                if excluded:
                    raise ServiceError(
                        "upstream_unavailable", "Scamalytics lookup failed", 502
                    ) from None
                raise
            result, error = None, None
            try:
                result = await self.client.lookup(lease.key, ip)
            except UpstreamError as exc:
                error = exc
                excluded.add(lease.key.id)
            finally:
                # Ambiguous failures retain their reserved credit; leases are always released.
                await self.pool.finish(
                    lease, credits=result.credits if result is not None else None, error=error
                )
            if result is not None:
                return Score(
                    ip=ip,
                    score=result.scamalytics_score,
                    risk=result.scamalytics_risk,
                    key_id=lease.key.id,
                    checked_at=datetime.now(UTC),
                )
        raise ServiceError("upstream_unavailable", "Scamalytics lookup failed", 502)

    async def batch(self, ips: list[str]) -> list[BatchItem]:
        # Fixed worker count per batch instead of one task per IP.
        iterator = iter(enumerate(ips))
        results: list[BatchItem | None] = [None] * len(ips)

        async def worker() -> None:
            for index, ip in iterator:
                try:
                    results[index] = BatchItem(ip=ip, result=await self.lookup(ip))
                except ServiceError as exc:
                    results[index] = BatchItem(
                        ip=ip, error=ErrorDetail(code=exc.code, message=exc.message)
                    )

        async with asyncio.TaskGroup() as group:
            for _ in range(min(self.settings.batch_workers, len(ips))):
                group.create_task(worker())
        return [item for item in results if item is not None]
