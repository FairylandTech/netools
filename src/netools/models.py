from datetime import datetime
from ipaddress import ip_address
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field


def normalize_ip(value: str) -> str:
    if "%" in value:
        raise ValueError("IPv6 scope ids are not supported")
    return str(ip_address(value))


IPAddress = Annotated[str, AfterValidator(normalize_ip)]
Risk = Literal["low", "medium", "high", "very high"]


class Score(BaseModel):
    ip: IPAddress
    score: int = Field(ge=0, le=100, strict=True)
    risk: Risk
    key_id: str
    checked_at: datetime
    cached: bool = False


class BatchRequest(BaseModel):
    ips: list[IPAddress] = Field(min_length=1, max_length=1000)


class ErrorDetail(BaseModel):
    code: str
    message: str


class BatchItem(BaseModel):
    ip: str
    result: Score | None = None
    error: ErrorDetail | None = None


class BatchResponse(BaseModel):
    results: list[BatchItem]


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 503):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)
