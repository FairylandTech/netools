from typing import Annotated

from fastapi import APIRouter, Query

from netools.api.deps import InfoPool, Lookup, protected
from netools.core.ip import IPAddress
from netools.ip_info.models import IpInfo
from netools.lookup.models import BatchRequest, BatchResponse

router = APIRouter(prefix="/ip/info", tags=["ip-info"], dependencies=protected)


@router.get("", response_model=IpInfo, summary="ipregistry address information for one IP")
async def get_info(lookup: Lookup, ip: Annotated[IPAddress, Query()]) -> IpInfo:
    return await lookup.info(ip)


@router.post("/batch", response_model=BatchResponse[IpInfo], summary="Information for many IPs")
async def batch_info(lookup: Lookup, body: BatchRequest) -> BatchResponse[IpInfo]:
    return BatchResponse(results=await lookup.batch(body.ips, lookup.info))


@router.get("/keys", summary="ipregistry account pool status (local state only)")
async def info_keys(pool: InfoPool) -> list[dict]:
    return await pool.snapshots()
