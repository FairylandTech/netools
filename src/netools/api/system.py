from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def ready(request: Request) -> dict:
    await request.app.state.redis.ping()
    return {"status": "ready"}
