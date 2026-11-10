import asyncio
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from redis.asyncio import Redis

from netools.config import KeyConfig, Settings
from netools.models import ServiceError
from netools.upstream import Credits, ScamalyticsClient, UpstreamError


@dataclass(frozen=True)
class Lease:
    key: KeyConfig
    state: str
    leases: str
    probe_lock: str
    token: str


class KeyPool:
    def __init__(
        self, redis: Redis, keys: list[KeyConfig], settings: Settings, client: ScamalyticsClient
    ):
        self.redis = redis
        self.keys = keys
        self.settings = settings
        self.client = client
        script_dir = Path(__file__).with_name("lua")
        self._reserve = redis.register_script((script_dir / "reserve.lua").read_text())
        self._finish = redis.register_script((script_dir / "finish.lua").read_text())
        self._cursor = random.randrange(len(keys))

    def state_keys(self, key: KeyConfig) -> tuple[str, str, str]:
        # Hash tags keep scripts' keys together if deployed on a Redis cluster.
        base = f"{self.settings.redis_prefix}:{{{key.account_hash}}}"
        month = datetime.now(UTC).strftime("%Y%m")
        return f"{base}:{month}", f"{base}:leases", f"{base}:{month}:probe"

    async def try_reserve(self, key: KeyConfig, *, probe: bool = False) -> tuple[str, Lease]:
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
        self, lease: Lease, *, credits: Credits | None = None, error: UpstreamError | None = None
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

    async def _probe(self, key: KeyConfig) -> None:
        status, lease = await self.try_reserve(key, probe=True)
        if status != "ok":
            return
        credits, error = None, None
        try:
            credits = await self.client.probe(key)
        except UpstreamError as exc:
            error = exc
        finally:
            await self.finish(lease, credits=credits, error=error)

    async def acquire(self, *, excluded: set[str] | None = None) -> Lease:
        excluded = excluded or set()
        deadline = time.monotonic() + self.settings.key_wait_seconds
        while True:
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
                if status == "needs_probe":
                    await self._probe(key)
                    status, lease = await self.try_reserve(key)
                    if status == "ok":
                        return lease
                pending |= status in ("busy", "needs_probe")
            if not pending or time.monotonic() >= deadline:
                raise ServiceError("no_key_available", "No API key is currently available")
            await asyncio.sleep(random.uniform(0.02, 0.06))

    async def snapshots(self) -> list[dict]:
        pipeline = self.redis.pipeline(transaction=False)
        for key in self.keys:
            pipeline.hgetall(self.state_keys(key)[0])
        states = await pipeline.execute()
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
            for key, state in zip(self.keys, states, strict=True)
        ]
