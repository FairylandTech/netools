from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from redis.asyncio import Redis

from netools.core.config import Settings
from netools.core.pool import KeyPool
from netools.core.scripts import read_lua
from netools.ip_risk.client import Credits, ScamalyticsClient, ScamalyticsError
from netools.ip_risk.config import ScamalyticsKeyConfig


@dataclass(frozen=True)
class Lease:
    key: ScamalyticsKeyConfig
    state: str
    leases: str
    probe_lock: str
    token: str


class ScamalyticsKeyPool(KeyPool[ScamalyticsKeyConfig, Lease]):
    """Monthly credit ledger per account with ``test=1`` quota probes."""

    pending_statuses = frozenset({"busy", "needs_probe"})

    def __init__(
        self,
        redis: Redis,
        keys: list[ScamalyticsKeyConfig],
        settings: Settings,
        client: ScamalyticsClient,
    ):
        super().__init__(redis, keys, settings)
        self.client = client
        self._reserve = redis.register_script(read_lua(__file__, "reserve.lua"))
        self._finish = redis.register_script(read_lua(__file__, "finish.lua"))

    def state_keys(self, key: ScamalyticsKeyConfig) -> tuple[str, str, str]:
        # Hash tags keep scripts' keys together if deployed on a Redis cluster.
        base = f"{self.settings.redis_prefix}:{{{key.account_hash}}}"
        month = datetime.now(UTC).strftime("%Y%m")
        return f"{base}:{month}", f"{base}:leases", f"{base}:{month}:probe"

    async def try_reserve(
        self, key: ScamalyticsKeyConfig, *, probe: bool = False
    ) -> tuple[str, Lease]:
        state, leases, lock = self.state_keys(key)
        lease = Lease(key, state, leases, lock, uuid4().hex)
        status = await self._reserve(
            keys=[state, leases, lock],
            args=[
                "probe" if probe else "live",
                lease.token,
                self.settings.lease_seconds * 1000,
                key.max_concurrency,
                key.requests_per_second,
                self.settings.quota_safety_margin,
                self.settings.max_probes_per_month,
            ],
        )
        return status, lease

    async def finish(
        self,
        lease: Lease,
        *,
        credits: Credits | None = None,
        error: ScamalyticsError | None = None,
    ) -> None:
        await self._finish(
            keys=[lease.state, lease.leases, lease.probe_lock],
            args=[
                lease.token,
                credits.remaining if credits is not None else -1,
                (credits.last_sync_timestamp_utc or "") if credits is not None else "",
                self.settings.quota_refresh_seconds * 1000,
                error.kind if error else "",
                error.cooldown * 1000 if error else 0,
            ],
        )

    async def _probe(self, key: ScamalyticsKeyConfig) -> None:
        status, lease = await self.try_reserve(key, probe=True)
        if status != "ok":
            return
        credits, error = None, None
        try:
            credits = await self.client.probe(key)
        except ScamalyticsError as exc:
            error = exc
        finally:
            await self.finish(lease, credits=credits, error=error)

    async def _recover(self, key: ScamalyticsKeyConfig, status: str) -> tuple[str, Lease] | None:
        if status != "needs_probe":
            return None
        await self._probe(key)
        return await self.try_reserve(key)

    async def snapshots(self) -> list[dict]:
        return [
            {
                "id": key.id,
                "remaining_estimate": int(state["remaining"]) if "remaining" in state else None,
                "reported_remaining": (
                    int(state["reported_remaining"]) if "reported_remaining" in state else None
                ),
                "safety_margin": self.settings.quota_safety_margin,
                "probes_this_month": int(state.get("probes", 0)),
                "observed_at_unix_ms": int(state.get("observed_at", 0)) or None,
                "blocked_until_unix_ms": int(state.get("blocked_until", 0)) or None,
                "last_error": state.get("last_error"),
            }
            for key, state in zip(self.keys, await self._states(), strict=True)
        ]
