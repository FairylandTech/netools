from dataclasses import dataclass
from ipaddress import ip_address

import httpx
from pydantic import ValidationError

from netools.core.http import retry_after
from netools.core.ip import normalize_ip
from netools.ip_info.config import IpregistryKeyConfig
from netools.ip_info.models import IpInfoData


class IpregistryError(Exception):
    def __init__(self, kind: str, cooldown: float = 30, *, retryable: bool = True):
        self.kind = kind
        self.cooldown = cooldown
        self.retryable = retryable
        super().__init__(f"Ipregistry request failed ({kind})")


@dataclass(frozen=True)
class IpregistryResult:
    data: IpInfoData
    remaining: int


class IpregistryClient:
    def __init__(self, http: httpx.AsyncClient, timeout: float):
        self.http = http
        self.timeout = timeout

    async def lookup(self, key: IpregistryKeyConfig, ip: str) -> IpregistryResult:
        ip = normalize_ip(ip)
        try:
            response = await self.http.get(
                f"https://api.ipregistry.co/{ip}",
                headers={
                    "Authorization": f"ApiKey {key.key.get_secret_value()}",
                    "Accept": "application/json",
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError:
            raise IpregistryError("network") from None
        if response.status_code in (401, 403, 451):
            raise IpregistryError("authentication", 300)
        if response.status_code == 402:
            raise IpregistryError("exhausted")
        if response.status_code == 429:
            raise IpregistryError("rate_limited", retry_after(response.headers.get("Retry-After")))
        if response.status_code >= 500:
            raise IpregistryError("server")
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            raise IpregistryError("protocol") from None
        # Do not expose message/resolution: they may echo credentials or request URLs.
        code = body.get("code")
        if isinstance(code, str):
            if code == "INSUFFICIENT_CREDITS":
                raise IpregistryError("exhausted")
            if code in {
                "MISSING_API_KEY",
                "INVALID_API_KEY",
                "DISABLED_API_KEY",
                "FORBIDDEN_IP",
                "FORBIDDEN_ORIGIN",
                "FORBIDDEN_IP_ORIGIN",
            }:
                raise IpregistryError("authentication", 300)
            if code == "TOO_MANY_REQUESTS":
                raise IpregistryError(
                    "rate_limited", retry_after(response.headers.get("Retry-After"))
                )
            if code in {"RESERVED_IP_ADDRESS", "INVALID_IP_ADDRESS"}:
                raise IpregistryError("unsupported_ip", 0, retryable=False)
            raise IpregistryError("application")
        if response.status_code != 200 or "error" in body or "message" in body:
            raise IpregistryError("protocol")
        try:
            data = IpInfoData.model_validate(body)
            remaining_header = response.headers["Ipregistry-Credits-Remaining"]
            if not remaining_header.isascii() or not remaining_header.isdecimal():
                raise ValueError
            remaining = int(remaining_header)
            if remaining > 10**12 or response.headers.get("Ipregistry-Credits-Consumed") != "1":
                raise ValueError
            if data.ip != ip or data.type != f"IPv{ip_address(ip).version}":
                raise ValueError
        except (ValidationError, ValueError, KeyError):
            raise IpregistryError("protocol") from None
        return IpregistryResult(data=data, remaining=remaining)
