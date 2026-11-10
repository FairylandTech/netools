from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from netools.config import KeyConfig
from netools.models import IPAddress, Risk


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


class UpstreamError(Exception):
    def __init__(self, kind: str, cooldown: float = 30):
        self.kind = kind
        self.cooldown = cooldown
        super().__init__(f"Scamalytics request failed ({kind})")


def retry_after(value: str | None) -> float:
    try:
        seconds = float(value) if value is not None else 30.0
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            seconds = 30.0
    # Persist a long Retry-After; the request can fail over to another account.
    return max(1.0, min(seconds, 86400.0))


class ScamalyticsClient:
    def __init__(self, http: httpx.AsyncClient, timeout: float):
        self.http = http
        self.timeout = timeout

    async def _request(self, key: KeyConfig, ip: str, *, test: bool) -> dict:
        params = {"key": key.key.get_secret_value(), "ip": ip}
        if test:
            params["test"] = "1"
        try:
            response = await self.http.get(key.endpoint, params=params, timeout=self.timeout)
        except httpx.HTTPError:
            # Never expose HTTPX exceptions: the request URL includes the API key.
            raise UpstreamError("network") from None
        if response.status_code in (401, 403):
            raise UpstreamError("authentication", 300)
        if response.status_code == 429:
            raise UpstreamError("rate_limited", retry_after(response.headers.get("Retry-After")))
        if response.status_code >= 500:
            raise UpstreamError("server")
        try:
            body = response.json()
            data = body.get("scamalytics", body)
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, AttributeError):
            raise UpstreamError("protocol") from None
        if data.get("status") == "error":
            message = str(data.get("error", "")).lower()
            if any(text in message for text in ("excess limit", "credits exhausted", "quota")):
                raise UpstreamError("exhausted")
            if any(text in message for text in ("unauthorized", "invalid api key")):
                raise UpstreamError("authentication", 300)
            raise UpstreamError("application")
        if response.status_code != 200:
            raise UpstreamError("protocol")
        return data

    async def probe(self, key: KeyConfig) -> Credits:
        data = await self._request(key, "8.8.8.8", test=True)
        try:
            return ProbeResult.model_validate(data).credits
        except ValidationError:
            raise UpstreamError("protocol") from None

    async def lookup(self, key: KeyConfig, ip: str) -> LiveResult:
        data = await self._request(key, ip, test=False)
        try:
            result = LiveResult.model_validate(data)
        except ValidationError:
            raise UpstreamError("protocol") from None
        if result.ip != ip:
            raise UpstreamError("protocol")
        return result
