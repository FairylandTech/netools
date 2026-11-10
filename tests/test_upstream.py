from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from netools.config import KeyConfig
from netools.upstream import ScamalyticsClient, UpstreamError, retry_after


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
        with pytest.raises(UpstreamError) as raised:
            await ScamalyticsClient(http, 1).lookup(
                KeyConfig(id="test", username="test", key="synthetic"), "8.8.8.8"
            )
        assert raised.value.kind == "protocol"


async def test_network_error_does_not_expose_key():
    secret = "never-expose-this-synthetic-secret"

    def fail(request):
        raise httpx.ConnectError(str(request.url), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        with pytest.raises(UpstreamError) as raised:
            await ScamalyticsClient(http, 1).lookup(
                KeyConfig(id="test", username="test", key=secret), "8.8.8.8"
            )
        assert secret not in str(raised.value)
        assert raised.value.__suppress_context__ is True


@pytest.mark.parametrize("body", [[], None, "unexpected", {"scamalytics": []}])
async def test_malformed_body_rejected(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as http:
        with pytest.raises(UpstreamError):
            await ScamalyticsClient(http, 1).probe(
                KeyConfig(id="test", username="test", key="synthetic")
            )


def test_retry_after_formats():
    assert retry_after("120") == 120
    assert retry_after("invalid") == 30
    assert retry_after("999999") == 86400
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=60), usegmt=True)
    assert 58 <= retry_after(future) <= 60
