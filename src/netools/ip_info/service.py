from datetime import UTC, datetime

from redis.asyncio import Redis

from netools.core.cache import SingleFlightCache
from netools.core.config import Settings
from netools.core.errors import ServiceError
from netools.core.ip import normalize_ip
from netools.ip_info.client import IpregistryClient, IpregistryError
from netools.ip_info.models import IpInfo
from netools.ip_info.pool import IpregistryKeyPool


class InfoService:
    """Cached, single-flight ipregistry lookups with account failover.

    Admission control and the total deadline belong to the caller
    (``netools.lookup.LookupService``); this service only knows about one IP.
    """

    def __init__(
        self,
        redis: Redis,
        pool: IpregistryKeyPool,
        client: IpregistryClient,
        settings: Settings,
    ):
        self.redis = redis
        self.pool = pool
        self.client = client
        self.settings = settings
        self.cache = SingleFlightCache(
            redis,
            settings,
            IpInfo,
            cache_prefix=f"{settings.redis_prefix}:ipregistry:result",
            lock_prefix=f"{settings.redis_prefix}:ipregistry:lookup",
        )

    async def lookup(self, ip: str) -> IpInfo:
        ip = normalize_ip(ip)
        return await self.cache.get_or_fetch(ip, lambda: self._fetch(ip))

    async def _fetch(self, ip: str) -> IpInfo:
        excluded: set[str] = set()
        for _ in range(self.settings.max_attempts):
            try:
                lease = await self.pool.acquire(excluded=excluded)
            except ServiceError:
                if excluded:
                    raise ServiceError(
                        "ipregistry_upstream_unavailable", "Ipregistry lookup failed", 502
                    ) from None
                raise
            result, error = None, None
            try:
                result = await self.client.lookup(lease.key, ip)
            except IpregistryError as exc:
                error = exc
                excluded.add(lease.key.id)
            finally:
                # Input-specific failures do not cool down a healthy account.
                await self.pool.finish(
                    lease,
                    remaining=result.remaining if result is not None else None,
                    error=error if error is not None and error.retryable else None,
                )
            if result is not None:
                return IpInfo(data=result.data, key_id=lease.key.id, checked_at=datetime.now(UTC))
            if error is not None and not error.retryable:
                raise ServiceError(
                    "ipregistry_unsupported_ip", "Ipregistry cannot look up this IP", 422
                )
        raise ServiceError("ipregistry_upstream_unavailable", "Ipregistry lookup failed", 502)
