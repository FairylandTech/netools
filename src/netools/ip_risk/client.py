from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from netools.core.http import retry_after
from netools.core.ip import IPAddress
from netools.ip_risk.config import ScamalyticsKeyConfig
from netools.ip_risk.models import Risk


class Credits(BaseModel):
    remaining: int = Field(ge=0, strict=True)
    used: int | None = Field(default=None, ge=0, strict=True)
    last_sync_timestamp_utc: str | None = None


class LiveResult(BaseModel):
    status: Literal["ok"]
    mode: Literal["live"]
    ip: IPAddress
    scamalytics_score: int = Field(ge=0, le=100, strict=True)
    scamalytics_risk: Risk
    credits: Credits


class ProbeResult(BaseModel):
    status: Literal["ok"]
    mode: Literal["test"]
    credits: Credits


class ScamalyticsError(Exception):
    def __init__(self, kind: str, cooldown: float = 30):
        self.kind = kind
        self.cooldown = cooldown
        super().__init__(f"Scamalytics request failed ({kind})")


class ScamalyticsClient:
    def __init__(self, http: httpx.AsyncClient, timeout: float):
        self.http = http
        self.timeout = timeout

    async def _request(self, key: ScamalyticsKeyConfig, ip: str, *, test: bool) -> dict:
        params = {"key": key.key.get_secret_value(), "ip": ip}
        if test:
            params["test"] = "1"
        try:
            response = await self.http.get(key.endpoint, params=params, timeout=self.timeout)
        except httpx.HTTPError:
            # Never expose HTTPX exceptions: the request URL includes the API key.
            raise ScamalyticsError("network") from None
        if response.status_code in (401, 403):
            raise ScamalyticsError("authentication", 300)
        if response.status_code == 429:
            raise ScamalyticsError("rate_limited", retry_after(response.headers.get("Retry-After")))
        if response.status_code >= 500:
            raise ScamalyticsError("server")
        try:
            body = response.json()
            data = body.get("scamalytics", body)
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, AttributeError):
            raise ScamalyticsError("protocol") from None
        if data.get("status") == "error":
            message = str(data.get("error", "")).lower()
            if any(text in message for text in ("excess limit", "credits exhausted", "quota")):
                raise ScamalyticsError("exhausted")
            if any(text in message for text in ("unauthorized", "invalid api key")):
                raise ScamalyticsError("authentication", 300)
            raise ScamalyticsError("application")
        if response.status_code != 200:
            raise ScamalyticsError("protocol")
        return data

    async def probe(self, key: ScamalyticsKeyConfig) -> Credits:
        data = await self._request(key, "8.8.8.8", test=True)
        try:
            return ProbeResult.model_validate(data).credits
        except ValidationError:
            raise ScamalyticsError("protocol") from None

    async def lookup(self, key: ScamalyticsKeyConfig, ip: str) -> LiveResult:
        data = await self._request(key, ip, test=False)
        try:
            result = LiveResult.model_validate(data)
        except ValidationError:
            raise ScamalyticsError("protocol") from None
        if result.ip != ip:
            raise ScamalyticsError("protocol")
        return result
