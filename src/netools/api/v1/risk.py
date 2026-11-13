from typing import Annotated

from fastapi import APIRouter, Query

from netools.api.deps import Lookup, RiskPool, protected
from netools.core.ip import IPAddress
from netools.ip_risk.models import RiskScore
from netools.lookup.models import BatchRequest, BatchResponse

router = APIRouter(prefix="/ip/risk", tags=["ip-risk"], dependencies=protected)


@router.get("", response_model=RiskScore, summary="Scamalytics fraud score for one IP")
async def get_risk(lookup: Lookup, ip: Annotated[IPAddress, Query()]) -> RiskScore:
    return await lookup.risk(ip)


@router.post("/batch", response_model=BatchResponse[RiskScore], summary="Scores for many IPs")
async def batch_risk(lookup: Lookup, body: BatchRequest) -> BatchResponse[RiskScore]:
    return BatchResponse(results=await lookup.batch(body.ips, lookup.risk))


@router.get("/keys", summary="Scamalytics account pool status (local state only)")
async def risk_keys(pool: RiskPool) -> list[dict]:
    return await pool.snapshots()
