"""Pre-0.2 paths kept for existing clients; new integrations use ``/v1/ip/*``."""

from typing import Annotated

from fastapi import APIRouter, Query

from netools.api.deps import InfoPool, Lookup, RiskPool, protected
from netools.core.errors import ErrorDetail
from netools.core.ip import IPAddress
from netools.ip_info.models import IpInfo
from netools.ip_risk.models import RiskScore
from netools.lookup.models import BatchRequest, BatchResponse, IpLookup

router = APIRouter(tags=["legacy"], dependencies=protected, deprecated=True)


class LegacyScore(RiskScore):
    ipregistry: IpInfo | None = None
    ipregistry_error: ErrorDetail | None = None


def flatten(result: IpLookup) -> LegacyScore:
    return LegacyScore(
        **result.risk.model_dump(), ipregistry=result.info, ipregistry_error=result.info_error
    )


@router.get("/score", response_model=LegacyScore, summary="Use GET /v1/ip/lookup")
async def score(lookup: Lookup, ip: Annotated[IPAddress, Query()]) -> LegacyScore:
    return flatten(await lookup.lookup(ip))


@router.post("/scores", response_model=BatchResponse[LegacyScore], summary="Use /ip/lookup/batch")
async def scores(lookup: Lookup, body: BatchRequest) -> BatchResponse[LegacyScore]:
    async def operation(ip: str) -> LegacyScore:
        return flatten(await lookup.lookup(ip))

    return BatchResponse(results=await lookup.batch(body.ips, operation))


@router.get("/keys", summary="Use /v1/ip/risk/keys and /v1/ip/info/keys")
async def keys(risk_pool: RiskPool, info_pool: InfoPool) -> dict:
    return {"keys": await risk_pool.snapshots(), "ipregistry_keys": await info_pool.snapshots()}
