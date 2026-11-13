"""Full HTTP chain with both providers; Redis is a stub, so Lua admission is not tested here."""

import asyncio
import json

import httpx
import pytest
from redis.exceptions import ConnectionError

from tests.support import registry_body


async def test_full_http_chain_combines_and_caches_both_providers(api_harness):
    h = api_harness
    response = await h.api.get("/v1/ip/lookup", params={"ip": "8.8.8.8"})
    assert response.status_code == 200
    body = response.json()
    assert body["ip"] == "8.8.8.8"
    assert body["risk"]["risk"] == "medium"
    assert body["info"]["data"] == registry_body()
    assert body["info_error"] is None
    assert "synthetic" not in response.text
    cached = (await h.api.get("/v1/ip/lookup", params={"ip": "8.8.8.8"})).json()
    assert cached["risk"]["cached"] and cached["info"]["cached"]
    assert len(h.calls) == 2
    assert h.state.reserve.await_count == 2
    assert h.state.finish.await_count == 2
    lookup = h.app.state.lookup
    assert lookup.risk_service.client.http is lookup.info_service.client.http


async def test_single_provider_endpoints_share_caches(api_harness):
    h = api_harness
    info = (await h.api.get("/v1/ip/info", params={"ip": "8.8.8.8"})).json()
    assert info["data"] == registry_body() and not info["cached"]
    assert "risk" not in info
    risk = (await h.api.get("/v1/ip/risk", params={"ip": "8.8.8.8"})).json()
    assert risk["score"] == 23 and not risk["cached"]
    combined = (await h.api.get("/v1/ip/lookup", params={"ip": "8.8.8.8"})).json()
    assert combined["risk"]["cached"] and combined["info"]["cached"]
    assert len(h.calls) == 2
    batch = await h.api.post("/v1/ip/info/batch", json={"ips": ["8.8.8.8", "1.1.1.1"]})
    items = batch.json()["results"]
    assert [item["result"]["data"]["ip"] for item in items] == ["8.8.8.8", "1.1.1.1"]
    assert items[0]["result"]["cached"] and not items[1]["result"]["cached"]


async def test_legacy_score_keeps_flat_shape(api_harness):
    h = api_harness
    legacy = (await h.api.get("/v1/score", params={"ip": "8.8.8.8"})).json()
    assert legacy["score"] == 23 and legacy["risk"] == "medium"
    assert legacy["ipregistry"]["data"] == registry_body()
    assert legacy["ipregistry_error"] is None
    batch = (await h.api.post("/v1/scores", json={"ips": ["8.8.8.8"]})).json()
    assert batch["results"][0]["result"]["ipregistry"]["cached"] is True


async def test_api_auth_validation_and_pool_status_without_network(api_harness):
    h = api_harness
    assert (
        await h.api.get(
            "/v1/ip/lookup", params={"ip": "8.8.8.8"}, headers={"Authorization": "Bearer invalid"}
        )
    ).status_code == 401
    assert (await h.api.get("/v1/ip/info", params={"ip": "invalid"})).status_code == 422
    h.state.pipeline.return_value.execute.side_effect = [[{}, {}], [{}], [{}, {}]]
    status = await h.api.get("/v1/ip/info/keys")
    assert status.status_code == 200
    assert len(status.json()) == 2
    assert "synthetic" not in status.text
    assert "account-one" not in status.text
    legacy = await h.api.get("/v1/keys")
    assert len(legacy.json()["ipregistry_keys"]) == 2
    assert h.calls == []


async def test_second_provider_failover_keeps_primary_result(api_harness):
    h = api_harness
    h.failure = lambda request: (
        httpx.Response(403, text="synthetic-secret")
        if request.headers["Authorization"] == "ApiKey synthetic-one"
        else httpx.Response(
            200,
            json=registry_body(request.url.path[1:]),
            headers={"Ipregistry-Credits-Remaining": "999", "Ipregistry-Credits-Consumed": "1"},
        )
    )
    result = (await h.api.get("/v1/ip/lookup", params={"ip": "8.8.8.8"})).json()
    assert result["info"]["key_id"] == "two"
    assert result["risk"]["score"] == 23
    assert len(h.calls) == 3
    args = h.state.finish.await_args_list[1].kwargs["args"]
    assert args[1:] == [-1, "authentication", 300000]


async def test_partial_error_not_cached_and_retry_reuses_risk(api_harness):
    h = api_harness
    h.failure = lambda _: httpx.Response(503, text="synthetic-secret")
    result = (await h.api.get("/v1/ip/lookup", params={"ip": "8.8.8.8"})).json()
    assert result["risk"]["score"] == 23
    assert result["info"] is None
    assert result["info_error"]["code"] == "ipregistry_upstream_unavailable"
    assert "synthetic" not in json.dumps(result)
    only_info = await h.api.get("/v1/ip/info", params={"ip": "8.8.8.8"})
    assert only_info.status_code == 502
    assert only_info.json()["error"]["code"] == "ipregistry_upstream_unavailable"
    h.failure = None
    again = (await h.api.get("/v1/ip/lookup", params={"ip": "8.8.8.8"})).json()
    assert again["risk"]["cached"] and again["info"] is not None
    assert sum(request.url.host != "api.ipregistry.co" for request in h.calls) == 1


async def test_unsupported_ip_not_retried_or_account_cooled(api_harness):
    h = api_harness
    h.failure = lambda _: httpx.Response(400, json={"code": "RESERVED_IP_ADDRESS"})
    result = (await h.api.get("/v1/ip/lookup", params={"ip": "127.0.0.1"})).json()
    assert result["info_error"]["code"] == "ipregistry_unsupported_ip"
    assert len(h.calls) == 2
    assert h.state.finish.await_args.kwargs["args"][1:] == [-1, "", 0]
    direct = await h.api.get("/v1/ip/info", params={"ip": "127.0.0.1"})
    assert direct.status_code == 422
    assert direct.json()["error"]["code"] == "ipregistry_unsupported_ip"


async def test_cancellation_releases_both_locks_and_ipregistry_lease(api_harness):
    h = api_harness
    h.delay = 10
    task = asyncio.create_task(h.app.state.lookup.lookup("8.8.8.8"))
    await asyncio.wait_for(h.started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not any(":lookup:" in key for key in h.state.cache)
    assert not any(":ipregistry:result:" in key for key in h.state.cache)
    assert h.state.finish.await_args.kwargs["args"][1:] == [-1, "", 0]
    assert h.app.state.lookup.pending == 0


async def test_total_deadline_keeps_risk_and_releases_resources(api_harness, settings):
    h = api_harness
    settings.lookup_timeout_seconds = 0.02
    h.delay = 10
    result = (await h.api.get("/v1/ip/lookup", params={"ip": "8.8.8.8"})).json()
    assert result["risk"]["score"] == 23
    assert result["info_error"]["code"] == "ipregistry_timeout"
    assert not any(":lookup:" in key for key in h.state.cache)
    assert h.state.finish.await_count == 2
    direct = await h.api.get("/v1/ip/info", params={"ip": "1.1.1.1"})
    assert direct.status_code == 504
    assert direct.json()["error"]["code"] == "lookup_timeout"
    assert direct.headers["Retry-After"] == "1"


async def test_redis_outage_stops_all_new_upstream_calls(api_harness):
    h = api_harness
    h.state.get.side_effect = ConnectionError("synthetic-secret")
    for path in ("/v1/ip/lookup", "/v1/ip/risk", "/v1/ip/info"):
        response = await h.api.get(path, params={"ip": "8.8.8.8"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "state_unavailable"
        assert "synthetic" not in response.text
    assert h.calls == []


async def test_batch_combines_preserves_order_and_coalesces_duplicates(api_harness):
    h = api_harness
    h.delay = 0.02
    ips = ["8.8.8.8", "2606:4700:4700::1111", "8.8.8.8"]
    response = await h.api.post("/v1/ip/lookup/batch", json={"ips": ips})
    items = response.json()["results"]
    assert [item["ip"] for item in items] == ips
    assert all(
        item["result"]["info"]["data"]["ip"] == ip for item, ip in zip(items, ips, strict=True)
    )
    assert len(h.calls) == 4
