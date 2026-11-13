import asyncio
import json

import httpx
import pytest

from netools.ip_risk.config import ScamalyticsKeyConfig
from netools.ip_risk.diagnostics import diagnose, response_summary


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "synthetic-secret",
        {"scamalytics": ["synthetic-secret"]},
        {
            "synthetic-secret": "synthetic-secret",
            "scamalytics": {
                "status": "synthetic-secret",
                "mode": {"synthetic-secret": "synthetic-secret"},
                "error": "synthetic-secret",
                "credits": {"remaining": "synthetic-secret"},
            },
        },
    ],
)
def test_response_summary_never_includes_untrusted_values(body):
    response = httpx.Response(
        200,
        content=json.dumps(body).encode(),
        headers={"Content-Type": "synthetic-secret", "Location": "synthetic-secret"},
        request=httpx.Request("GET", "https://example.test/synthetic-secret?key=synthetic-secret"),
    )
    summary = response_summary(response)
    assert summary["http_status"] == 200
    assert summary["format"] == "json"
    assert "synthetic-secret" not in json.dumps(summary)


def test_schema_diagnostics_report_only_field_paths_and_types():
    summary = response_summary(
        httpx.Response(
            200,
            json={"scamalytics": {"status": "ok", "credits": {"remaining": "secret"}}},
        )
    )
    assert summary["mode"] == "missing"
    assert summary["credit_field_types"]["remaining"] == "str"
    assert summary["validation_errors"] == [
        {"field": "mode", "type": "missing"},
        {"field": "credits.remaining", "type": "int_type"},
    ]
    assert "secret" not in json.dumps(summary)


async def test_diagnostics_use_one_budgeted_probe_and_skip_fresh_state(harness_factory, redis):
    harness = harness_factory()
    first = await diagnose(harness.pool)
    assert first["accounts"][0]["result"] == "ok"
    assert first["accounts"][0]["response"]["mode"] == "test"
    assert first["keys"][0]["probes_this_month"] == 1
    assert first["keys"][0]["remaining_estimate"] == 1000
    assert await redis.zcard(harness.pool.state_keys(harness.keys[0])[1]) == 0

    second = await diagnose(harness.pool)
    assert second["accounts"][0]["reservation"] == "fresh"
    assert second["accounts"][0]["probe_sent"] is False
    assert len(harness.mock.calls) == 1
    assert harness.mock.calls[0][1] is True
    assert harness.client.http.event_hooks["response"] == []


async def test_concurrent_diagnostics_share_probe_lock(harness_factory):
    first = harness_factory()
    second = harness_factory(keys=first.keys, mock=first.mock)
    first.mock.delay = 0.05
    reports = await asyncio.gather(diagnose(first.pool), diagnose(second.pool))
    assert sum(report["accounts"][0]["probe_sent"] for report in reports) == 1
    assert len(first.mock.calls) == 1
    assert first.mock.calls[0][1] is True


async def test_failed_diagnostics_redact_body_and_preserve_cooldown(harness_factory, redis):
    key = ScamalyticsKeyConfig(id="one", username="private-user", key="synthetic-private-key")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(404, text=f"<html>{request.url}</html>")

    harness = harness_factory(keys=[key], mock=respond)
    report = await diagnose(harness.pool)
    account = report["accounts"][0]
    assert account["result"] == "protocol"
    assert account["response"] == {"http_status": 404, "format": "non_json"}
    assert report["keys"][0]["last_error"] == "protocol"
    assert report["keys"][0]["remaining_estimate"] is None
    assert report["keys"][0]["probes_this_month"] == 1
    encoded = json.dumps(report)
    assert key.username not in encoded
    assert key.key.get_secret_value() not in encoded
    assert "<html>" not in encoded
    assert "scamalytics.com" not in encoded
    assert await redis.zcard(harness.pool.state_keys(key)[1]) == 0

    again = await diagnose(harness.pool)
    assert again["accounts"][0]["reservation"] == "cooldown"
    assert len(calls) == 1
    assert calls[0].url.params["test"] == "1"


async def test_diagnostics_respect_probe_budget(harness_factory, settings, redis):
    harness = harness_factory(config=settings.model_copy(update={"max_probes_per_month": 1}))
    key = harness.keys[0]
    harness.mock.failures[key.username] = "probe"
    await diagnose(harness.pool)
    await redis.hset(harness.pool.state_keys(key)[0], "blocked_until", 0)
    report = await diagnose(harness.pool)
    assert report["accounts"][0]["reservation"] == "probe_limit"
    assert len(harness.mock.calls) == 1


async def test_cancelled_diagnostic_releases_lease_without_refunding_probe(harness_factory, redis):
    started = asyncio.Event()
    waiting = asyncio.Event()

    async def respond(request):
        started.set()
        await waiting.wait()
        return httpx.Response(200)

    harness = harness_factory(mock=respond)
    task = asyncio.create_task(diagnose(harness.pool))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state, leases, probe_lock = harness.pool.state_keys(harness.keys[0])
    assert await redis.hget(state, "probes") == "1"
    assert await redis.zcard(leases) == 0
    assert not await redis.exists(probe_lock)
    assert harness.client.http.event_hooks["response"] == []
