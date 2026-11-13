from datetime import UTC, datetime

from redis.asyncio import Redis

from netools.core.cache import SingleFlightCache
from netools.core.config import Settings
from netools.core.errors import ServiceError
from netools.core.ip import normalize_ip
from netools.ip_risk.client import ScamalyticsClient, ScamalyticsError
from netools.ip_risk.models import RiskScore
from netools.ip_risk.pool import ScamalyticsKeyPool


class RiskService:
    """Cached, single-flight risk lookups with account failover.

    Admission control and the total deadline belong to the caller
    (``netools.lookup.LookupService``); this service only knows about one IP.
    """

    def __init__(
        self,
        redis: Redis,
        pool: ScamalyticsKeyPool,
        client: ScamalyticsClient,
        settings: Settings,
    ):
        self.redis = redis
        self.pool = pool
        self.client = client
        self.settings = settings
        self.cache = SingleFlightCache(
            redis,
            settings,
            RiskScore,
            cache_prefix=f"{settings.redis_prefix}:score",
            lock_prefix=f"{settings.redis_prefix}:lookup",
        )

    async def lookup(self, ip: str) -> RiskScore:
        ip = normalize_ip(ip)
        return await self.cache.get_or_fetch(ip, lambda: self._fetch(ip))

    async def _fetch(self, ip: str) -> RiskScore:
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
            except ScamalyticsError as exc:
                error = exc
                excluded.add(lease.key.id)
            finally:
                # Ambiguous failures retain their reserved credit; leases are always released.
                await self.pool.finish(
                    lease, credits=result.credits if result is not None else None, error=error
                )
            if result is not None:
                return RiskScore(
                    ip=ip,
                    score=result.scamalytics_score,
                    risk=result.scamalytics_risk,
                    key_id=lease.key.id,
                    checked_at=datetime.now(UTC),
                )
        raise ServiceError("upstream_unavailable", "Scamalytics lookup failed", 502)
