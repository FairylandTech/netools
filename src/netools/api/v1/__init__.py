from fastapi import APIRouter

from netools.api.v1 import info, legacy, lookup, risk

router = APIRouter(prefix="/v1")
router.include_router(risk.router)
router.include_router(info.router)
router.include_router(lookup.router)
router.include_router(legacy.router)
