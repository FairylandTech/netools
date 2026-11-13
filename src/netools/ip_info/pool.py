from dataclasses import dataclass
from uuid import uuid4

from redis.asyncio import Redis

from netools.core.config import Settings
from netools.core.pool import KeyPool
from netools.core.scripts import read_lua
from netools.ip_info.client import IpregistryError
from netools.ip_info.config import IpregistryKeyConfig


@dataclass(frozen=True)
class Lease:
    key: IpregistryKeyConfig
    state: str
    leases: str
    token: str


class IpregistryKeyPool(KeyPool[IpregistryKeyConfig, Lease]):
    """Persistent (non-monthly) credit ledger seeded from ``initial_credits``."""

    unavailable_code = "ipregistry_no_key_available"
    unavailable_message = "No ipregistry API key is available"

    def __init__(self, redis: Redis, keys: list[IpregistryKeyConfig], settings: Settings):
        super().__init__(redis, keys, settings)
        self._reserve = redis.register_script(read_lua(__file__, "reserve.lua"))
        self._finish = redis.register_script(read_lua(__file__, "finish.lua"))

    def state_keys(self, key: IpregistryKeyConfig) -> tuple[str, str]:
        base = f"{self.settings.redis_prefix}:{{ipregistry:{key.account_hash}}}"
        return f"{base}:state", f"{base}:leases"

    async def try_reserve(self, key: IpregistryKeyConfig) -> tuple[str, Lease]:
        state, leases = self.state_keys(key)
        lease = Lease(key, state, leases, uuid4().hex)
        status = await self._reserve(
            keys=[state, leases],
            args=[
                lease.token,
                self.settings.lease_seconds * 1000,
                key.max_concurrency,
                key.requests_per_second,
                self.settings.ipregistry_quota_safety_margin,
                key.initial_credits,
            ],
        )
        return status, lease

    async def finish(
        self,
        lease: Lease,
        *,
        remaining: int | None = None,
        error: IpregistryError | None = None,
    ) -> None:
        await self._finish(
            keys=[lease.state, lease.leases],
            args=[
                lease.token,
                remaining if remaining is not None else -1,
                error.kind if error else "",
                error.cooldown * 1000 if error else 0,
            ],
        )

    async def snapshots(self) -> list[dict]:
        return [
            {
                "id": key.id,
                "remaining_estimate": int(state["remaining"]) if "remaining" in state else None,
                "reported_remaining": (
                    int(state["reported_remaining"]) if "reported_remaining" in state else None
                ),
                "initial_credits": key.initial_credits,
                "safety_margin": self.settings.ipregistry_quota_safety_margin,
                "observed_at_unix_ms": int(state.get("observed_at", 0)) or None,
                "blocked_until_unix_ms": int(state.get("blocked_until", 0)) or None,
                "last_error": state.get("last_error"),
            }
            for key, state in zip(self.keys, await self._states(), strict=True)
        ]
