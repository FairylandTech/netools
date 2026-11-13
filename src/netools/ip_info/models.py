from datetime import datetime
from typing import Literal

from pydantic import BaseModel, JsonValue

from netools.core.ip import IPAddress


class IpInfoData(BaseModel):
    # Only documented groups are exposed; error/debug envelope fields are discarded.
    ip: IPAddress
    type: Literal["IPv4", "IPv6"]
    hostname: str | None
    carrier: dict[str, JsonValue] | None
    company: dict[str, JsonValue] | None
    connection: dict[str, JsonValue] | None
    currency: dict[str, JsonValue] | None
    location: dict[str, JsonValue] | None
    security: dict[str, JsonValue] | None
    time_zone: dict[str, JsonValue] | None


class IpInfo(BaseModel):
    data: IpInfoData
    key_id: str
    checked_at: datetime
    cached: bool = False
