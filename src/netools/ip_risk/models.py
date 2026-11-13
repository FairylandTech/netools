from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from netools.core.ip import IPAddress

Risk = Literal["low", "medium", "high", "very high"]


class RiskScore(BaseModel):
    ip: IPAddress
    score: int = Field(ge=0, le=100, strict=True)
    risk: Risk
    key_id: str
    checked_at: datetime
    cached: bool = False
