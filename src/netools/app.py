import logging
import secrets
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from redis.exceptions import RedisError

from netools.config import Settings
from netools.models import BatchRequest, BatchResponse, IPAddress, Score, ServiceError
from netools.pool import KeyPool
from netools.service import ScoreService
from netools.upstream import ScamalyticsClient


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings or Settings()
        keys = config.load_keys()
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
            async with httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=config.http_max_connections,
                    max_keepalive_connections=config.http_max_connections,
                ),
                follow_redirects=False,
                trust_env=False,
            ) as http:
                upstream = ScamalyticsClient(http, config.upstream_timeout_seconds)
                pool = KeyPool(redis, keys, config, upstream)
                app.state.settings = config
                app.state.redis = redis
                app.state.pool = pool
                app.state.service = ScoreService(redis, pool, upstream, config)
                yield
        finally:
            await redis.aclose()

    app = FastAPI(title="Scamalytics IP Score", version="0.1.0", lifespan=lifespan)
    bearer = HTTPBearer(auto_error=False)

    async def authenticate(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        expected = request.app.state.settings.service_api_token.get_secret_value()
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode(), expected.encode()
        ):
            raise ServiceError("unauthorized", "A valid Bearer token is required", 401)

    protected = [Depends(authenticate)]

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else {}
        if exc.status_code in (503, 504):
            headers["Retry-After"] = "1"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @app.exception_handler(RedisError)
    async def redis_error(_: Request, __: RedisError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "state_unavailable", "message": "Redis is unavailable"}},
            headers={"Retry-After": "1"},
        )

    @app.get("/healthz")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready(request: Request) -> dict:
        await request.app.state.redis.ping()
        return {"status": "ready"}

    @app.get("/v1/score", response_model=Score, dependencies=protected)
    async def score(request: Request, ip: Annotated[IPAddress, Query()]) -> Score:
        return await request.app.state.service.lookup(ip)

    @app.post("/v1/scores", response_model=BatchResponse, dependencies=protected)
    async def scores(request: Request, body: BatchRequest) -> BatchResponse:
        return BatchResponse(results=await request.app.state.service.batch(body.ips))

    @app.get("/v1/keys", dependencies=protected)
    async def key_status(request: Request) -> dict:
        return {"keys": await request.app.state.pool.snapshots()}

    return app
