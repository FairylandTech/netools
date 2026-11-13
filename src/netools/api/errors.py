from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from netools.core.errors import ServiceError


def register_exception_handlers(app: FastAPI) -> None:
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
