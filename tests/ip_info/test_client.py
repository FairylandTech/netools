import json

import httpx
import pytest

from netools.ip_info.client import IpregistryClient, IpregistryError
from tests.support import registry_body, registry_key, registry_response


@pytest.mark.parametrize("ip", ["8.8.8.8", "2606:4700:4700::1111"])
async def test_single_lookup_auth_headers_and_complete_data(ip):
    calls = []

    def respond(request):
        calls.append(request)
        return registry_response(ip)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await IpregistryClient(http, 1).lookup(registry_key(), ip)
    assert result.data.model_dump() == registry_body(ip)
    assert result.remaining == 999
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.host == "api.ipregistry.co"
    assert calls[0].url.path == f"/{ip}"
    assert not calls[0].url.query
    assert calls[0].headers["Authorization"] == "ApiKey synthetic-one"


@pytest.mark.parametrize(
    ("status", "code", "kind", "retryable"),
    [
        (401, None, "authentication", True),
        (403, None, "authentication", True),
        (451, None, "authentication", True),
        (402, None, "exhausted", True),
        (429, None, "rate_limited", True),
        (500, None, "server", True),
        (200, "INVALID_API_KEY", "authentication", True),
        (200, "INSUFFICIENT_CREDITS", "exhausted", True),
        (200, "TOO_MANY_REQUESTS", "rate_limited", True),
        (400, "RESERVED_IP_ADDRESS", "unsupported_ip", False),
        (400, "INVALID_IP_ADDRESS", "unsupported_ip", False),
        (200, "UNRECOGNIZED", "application", True),
    ],
)
async def test_errors_are_classified_and_redacted(status, code, kind, retryable):
    body = {"code": code, "message": "synthetic-secret", "resolution": "synthetic-secret"}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json=body, headers={"Retry-After": "120"})
        )
    ) as http:
        with pytest.raises(IpregistryError) as raised:
            await IpregistryClient(http, 1).lookup(registry_key(), "8.8.8.8")
    assert raised.value.kind == kind
    assert raised.value.retryable is retryable
    assert "synthetic-secret" not in str(raised.value)
    if kind == "rate_limited":
        assert raised.value.cooldown == 120


@pytest.mark.parametrize(
    "fault",
    [
        "array",
        "html",
        "wrong_ip",
        "wrong_type",
        "missing_group",
        "missing_credits",
        "bad_credits",
        "bad_cost",
        "error_envelope",
    ],
)
async def test_invalid_success_payloads_rejected(fault):
    response = registry_response()
    body = registry_body()
    if fault == "array":
        body = []
    elif fault == "wrong_ip":
        body["ip"] = "1.1.1.1"
    elif fault == "wrong_type":
        body["type"] = "IPv6"
    elif fault == "missing_group":
        del body["location"]
    elif fault == "error_envelope":
        body["error"] = "synthetic-secret"
    elif fault == "missing_credits":
        del response.headers["Ipregistry-Credits-Remaining"]
    elif fault == "bad_credits":
        response.headers["Ipregistry-Credits-Remaining"] = "-1"
    elif fault == "bad_cost":
        response.headers["Ipregistry-Credits-Consumed"] = "2"
    response = httpx.Response(
        200,
        headers=response.headers,
        content=(
            b"<html>synthetic-secret</html>" if fault == "html" else json.dumps(body).encode()
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as http:
        with pytest.raises(IpregistryError, match="protocol"):
            await IpregistryClient(http, 1).lookup(registry_key(), "8.8.8.8")


async def test_network_error_redacts_credentials():
    def fail(request):
        raise httpx.ConnectError(request.headers["Authorization"], request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        with pytest.raises(IpregistryError) as raised:
            await IpregistryClient(http, 1).lookup(registry_key(), "8.8.8.8")
    assert raised.value.kind == "network"
    assert "synthetic" not in str(raised.value)
    assert raised.value.__suppress_context__
