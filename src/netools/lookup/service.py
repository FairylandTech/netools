import asyncio
from collections.abc import Awaitable, Callable

from redis.exceptions import RedisError

from netools.core.admission import AdmissionGate
from netools.core.config import Settings
from netools.core.errors import ErrorDetail, ServiceError
from netools.core.ip import normalize_ip
from netools.ip_info.models import IpInfo
from netools.ip_info.service import InfoService
from netools.ip_risk.models import RiskScore
from netools.ip_risk.service import RiskService
from netools.lookup.models import BatchItem, IpLookup

INFO_NOT_CONFIGURED = ErrorDetail(
    code="ipregistry_not_configured", message="Ipregistry is not configured"
)
INFO_TIMEOUT = ErrorDetail(
    code="ipregistry_timeout", message="The ipregistry lookup deadline was exceeded"
)
STATE_UNAVAILABLE = ErrorDetail(code="state_unavailable", message="Redis is unavailable")


class LookupService:
    """Applies per-process admission and the total deadline to provider lookups.

    Every public method accepts one IP, runs under the shared ``AdmissionGate`` and
    ``LOOKUP_TIMEOUT_SECONDS``, and maps infrastructure failures to ``ServiceError``.
    ``batch`` runs any of them over many IPs with a fixed worker count.
    """

    def __init__(self, settings: Settings, risk: RiskService, info: InfoService | None = None):
        self.settings = settings
        self.risk_service = risk
        self.info_service = info
        self.gate = AdmissionGate(settings.max_pending_lookups)

    @property
    def pending(self) -> int:
        return self.gate.active

    async def _guarded[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        with self.gate:
            try:
                async with asyncio.timeout(self.settings.lookup_timeout_seconds):
                    return await operation()
            except TimeoutError:
                raise ServiceError(
                    "lookup_timeout", "The lookup deadline was exceeded", 504
                ) from None
            except RedisError:
                raise ServiceError(*STATE_UNAVAILABLE.model_dump().values()) from None

    async def risk(self, ip: str) -> RiskScore:
        ip = normalize_ip(ip)
        return await self._guarded(lambda: self.risk_service.lookup(ip))

    async def info(self, ip: str) -> IpInfo:
        ip = normalize_ip(ip)
        if self.info_service is None:
            raise ServiceError(INFO_NOT_CONFIGURED.code, INFO_NOT_CONFIGURED.message)
        return await self._guarded(lambda: self.info_service.lookup(ip))

    async def lookup(self, ip: str) -> IpLookup:
        """Risk first, then info within the same deadline; info failures are reported inline."""
        ip = normalize_ip(ip)
        risk: RiskScore | None = None
        with self.gate:
            try:
                async with asyncio.timeout(self.settings.lookup_timeout_seconds):
                    risk = await self.risk_service.lookup(ip)
                    if self.info_service is None:
                        return IpLookup(ip=ip, risk=risk, info_error=INFO_NOT_CONFIGURED)
                    try:
                        info = await self.info_service.lookup(ip)
                    except ServiceError as exc:
                        return IpLookup(ip=ip, risk=risk, info_error=exc.detail())
                    return IpLookup(ip=ip, risk=risk, info=info)
            except TimeoutError:
                if risk is not None:
                    return IpLookup(ip=ip, risk=risk, info_error=INFO_TIMEOUT)
                raise ServiceError(
                    "lookup_timeout", "The lookup deadline was exceeded", 504
                ) from None
            except RedisError:
                if risk is not None:
                    return IpLookup(ip=ip, risk=risk, info_error=STATE_UNAVAILABLE)
                raise ServiceError(STATE_UNAVAILABLE.code, STATE_UNAVAILABLE.message) from None

    async def batch[T](
        self, ips: list[str], operation: Callable[[str], Awaitable[T]]
    ) -> list[BatchItem[T]]:
        # Fixed worker count per batch instead of one task per IP; order is preserved.
        iterator = iter(enumerate(ips))
        results: list[BatchItem[T] | None] = [None] * len(ips)

        async def worker() -> None:
            for index, ip in iterator:
                try:
                    results[index] = BatchItem(ip=ip, result=await operation(ip))
                except ServiceError as exc:
                    results[index] = BatchItem(ip=ip, error=exc.detail())

        async with asyncio.TaskGroup() as group:
            for _ in range(min(self.settings.batch_workers, len(ips))):
                group.create_task(worker())
        return [item for item in results if item is not None]
