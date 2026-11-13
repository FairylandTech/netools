import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from netools import __version__
from netools.api import system, v1
from netools.api.errors import register_exception_handlers
from netools.core.config import Settings
from netools.ip_info.client import IpregistryClient
from netools.ip_info.config import load_ipregistry_keys
from netools.ip_info.pool import IpregistryKeyPool
from netools.ip_info.service import InfoService
from netools.ip_risk.client import ScamalyticsClient
from netools.ip_risk.config import load_scamalytics_keys
from netools.ip_risk.pool import ScamalyticsKeyPool
from netools.ip_risk.service import RiskService
from netools.lookup.service import LookupService


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings or Settings()
        risk_keys = load_scamalytics_keys(config.scamalytics_keys_file)
        info_keys = load_ipregistry_keys(config.ipregistry_keys_file)
        # HTTPX's INFO request logging contains upstream query-string credentials.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        redis = Redis.from_url(
            config.redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            max_connections=config.max_pending_lookups * 2 + 32,
        )
        try:
            await redis.ping()
            # One HTTPX client is shared by every provider for the life of the process.
            async with httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=config.http_max_connections,
                    max_keepalive_connections=config.http_max_connections,
                ),
                follow_redirects=False,
                trust_env=False,
            ) as http:
                timeout = config.upstream_timeout_seconds
                risk_client = ScamalyticsClient(http, timeout)
                risk_pool = ScamalyticsKeyPool(redis, risk_keys, config, risk_client)
                info_pool = IpregistryKeyPool(redis, info_keys, config)
                info = (
                    InfoService(redis, info_pool, IpregistryClient(http, timeout), config)
                    if info_keys
                    else None
                )
                app.state.settings = config
                app.state.redis = redis
                app.state.risk_pool = risk_pool
                app.state.info_pool = info_pool
                app.state.lookup = LookupService(
                    config, RiskService(redis, risk_pool, risk_client, config), info
                )
                yield
        finally:
            await redis.aclose()

    app = FastAPI(title="netools IP lookup", version=__version__, lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(system.router)
    app.include_router(v1.router)
    return app
