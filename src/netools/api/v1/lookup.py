from typing import Annotated

from fastapi import APIRouter, Query

from netools.api.deps import Lookup, protected
from netools.core.ip import IPAddress
from netools.lookup.models import BatchRequest, BatchResponse, IpLookup

router = APIRouter(prefix="/ip/lookup", tags=["ip-lookup"], dependencies=protected)


@router.get("", response_model=IpLookup, summary="Risk score plus address information")
async def get_lookup(lookup: Lookup, ip: Annotated[IPAddress, Query()]) -> IpLookup:
    return await lookup.lookup(ip)


@router.post(
    "/batch", response_model=BatchResponse[IpLookup], summary="Combined results for many IPs"
)
async def batch_lookup(lookup: Lookup, body: BatchRequest) -> BatchResponse[IpLookup]:
    return BatchResponse(results=await lookup.batch(body.ips, lookup.lookup))
