from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from netools.core.http import retry_after
from netools.ip_risk.client import ScamalyticsClient, ScamalyticsError
from netools.ip_risk.config import ScamalyticsKeyConfig


@pytest.mark.parametrize("invalid", ["test", "wrong_ip", "string_score", "invalid_score"])
async def test_invalid_live_payload_rejected(invalid):
    data = {
        "status": "ok",
        "mode": "live",
        "ip": "8.8.8.8",
        "scamalytics_score": 0,
        "scamalytics_risk": "low",
        "credits": {"remaining": 100},
    }
    if invalid == "test":
        data["mode"] = "test"
    elif invalid == "wrong_ip":
        data["ip"] = "1.1.1.1"
    elif invalid == "string_score":
        data["scamalytics_score"] = "0"
    else:
        data["scamalytics_score"] = 101

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"scamalytics": data}))
    ) as http:
        with pytest.raises(ScamalyticsError) as raised:
            await ScamalyticsClient(http, 1).lookup(
                ScamalyticsKeyConfig(id="test", username="test", key="synthetic"), "8.8.8.8"
            )
        assert raised.value.kind == "protocol"


async def test_network_error_does_not_expose_key():
    secret = "never-expose-this-synthetic-secret"

    def fail(request):
        raise httpx.ConnectError(str(request.url), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        with pytest.raises(ScamalyticsError) as raised:
            await ScamalyticsClient(http, 1).lookup(
                ScamalyticsKeyConfig(id="test", username="test", key=secret), "8.8.8.8"
            )
        assert secret not in str(raised.value)
        assert raised.value.__suppress_context__ is True


@pytest.mark.parametrize("body", [[], None, "unexpected", {"scamalytics": []}])
async def test_malformed_body_rejected(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as http:
        with pytest.raises(ScamalyticsError):
            await ScamalyticsClient(http, 1).probe(
                ScamalyticsKeyConfig(id="test", username="test", key="synthetic")
            )


def test_retry_after_formats():
    assert retry_after("120") == 120
    assert retry_after("invalid") == 30
    assert retry_after("999999") == 86400
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=60), usegmt=True)
    assert 58 <= retry_after(future) <= 60


async def test_api13_preflight_and_live_request_use_assigned_host():
    requests = []

    def respond(request):
        requests.append(request)
        test = request.url.params.get("test") == "1"
        return httpx.Response(
            200,
            json={
                "scamalytics": {
                    "status": "ok",
                    "mode": "test" if test else "live",
                    "ip": request.url.params["ip"],
                    "scamalytics_score": 17,
                    "scamalytics_risk": "low",
                    "credits": {"remaining": 100},
                }
            },
        )

    key = ScamalyticsKeyConfig(
        id="test", username="synthetic-user", key="synthetic-key", api_host="api13"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = ScamalyticsClient(http, 1)
        assert (await client.probe(key)).remaining == 100
        assert (await client.lookup(key, "1.1.1.1")).scamalytics_score == 17

    assert len(requests) == 2
    assert all(request.url.host == "api13.scamalytics.com" for request in requests)
    assert all(request.url.path == "/v3/synthetic-user/" for request in requests)
    assert all(request.url.params["key"] == "synthetic-key" for request in requests)
    assert requests[0].url.params["test"] == "1"
    assert "test" not in requests[1].url.params
