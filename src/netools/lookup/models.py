from pydantic import BaseModel, Field

from netools.core.errors import ErrorDetail
from netools.core.ip import IPAddress
from netools.ip_info.models import IpInfo
from netools.ip_risk.models import RiskScore


class IpLookup(BaseModel):
    """Combined result: risk is required, info is best-effort."""

    ip: IPAddress
    risk: RiskScore
    info: IpInfo | None = None
    info_error: ErrorDetail | None = None


class BatchRequest(BaseModel):
    ips: list[IPAddress] = Field(min_length=1, max_length=1000)


class BatchItem[T](BaseModel):
    ip: str
    result: T | None = None
    error: ErrorDetail | None = None


class BatchResponse[T](BaseModel):
    results: list[BatchItem[T]]
