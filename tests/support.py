"""Protocol doubles shared across test packages. No real credentials or hosts."""

import asyncio
from collections import Counter

import httpx

from netools.ip_info.config import IpregistryKeyConfig
from netools.ip_risk.config import ScamalyticsKeyConfig


class MockScamalytics:
    def __init__(self, keys: list[ScamalyticsKeyConfig]):
        self.remaining = dict.fromkeys((key.username for key in keys), 1000)
        self.failures: dict[str, str] = {}
        self.calls: list[tuple[str, bool, str]] = []
        self.active = Counter()
        self.peak = Counter()
        self.delay = 0.005
        self.live_started = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        username = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        test = request.url.params.get("test") == "1"
        ip = request.url.params["ip"]
        self.calls.append((username, test, ip))
        self.active[username] += 1
        self.peak[username] = max(self.peak[username], self.active[username])
        try:
            if not test:
                self.live_started.set()
            await asyncio.sleep(self.delay)
            failure = self.failures.get(username)
            if failure == "probe" and test:
                return httpx.Response(500)
            if failure == "authentication" and not test:
                return httpx.Response(401, text=f"secret: {request.url.params['key']}")
            if failure == "rate_limited" and not test:
                return httpx.Response(429, headers={"Retry-After": "120"})
            if (failure == "exhausted" and not test) or self.remaining[username] <= 0:
                return httpx.Response(
                    200,
                    json={
                        "scamalytics": {
                            "status": "error",
                            "error": "Excess limit: credits exhausted",
                        }
                    },
                )
            if not test:
                self.remaining[username] -= 1
            return httpx.Response(
                200,
                json={
                    "scamalytics": {
                        "status": "ok",
                        "mode": "test" if test else "live",
                        "ip": ip,
                        "scamalytics_score": 23,
                        "scamalytics_risk": "medium",
                        "credits": {"remaining": self.remaining[username]},
                    }
                },
            )
        finally:
            self.active[username] -= 1


def scamalytics_live_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "scamalytics": {
                "status": "ok",
                "mode": "live",
                "ip": request.url.params["ip"],
                "scamalytics_score": 23,
                "scamalytics_risk": "medium",
                "credits": {"remaining": 999},
            }
        },
    )


def registry_body(ip="8.8.8.8"):
    return {
        "ip": ip,
        "type": "IPv6" if ":" in ip else "IPv4",
        "hostname": None,
        "carrier": {"name": None, "mcc": None, "mnc": None},
        "company": {"name": "Example", "domain": "example.test", "type": "hosting"},
        "connection": {"asn": 15169, "route": "8.8.8.0/24"},
        "currency": {"code": "USD"},
        "location": {"country": {"code": "US"}, "latitude": 37.4},
        "security": {"is_vpn": False, "is_proxy": False},
        "time_zone": {"id": "America/Los_Angeles", "offset": -28800},
    }


def registry_response(ip="8.8.8.8", remaining=999):
    return httpx.Response(
        200,
        json=registry_body(ip),
        headers={
            "Ipregistry-Credits-Remaining": str(remaining),
            "Ipregistry-Credits-Consumed": "1",
        },
    )


def registry_key(name="one", **kwargs):
    return IpregistryKeyConfig(
        id=name,
        account_id=f"account-{name}",
        key=f"synthetic-{name}",
        initial_credits=1000,
        requests_per_second=10000,
        **kwargs,
    )
