"""Inspect quota preflight responses without disclosing upstream credentials."""

import asyncio
import json
import logging

import httpx
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from netools.config import Settings
from netools.pool import KeyPool
from netools.upstream import ProbeResult, ScamalyticsClient, UpstreamError


def field_type(data: dict, field: str) -> str:
    return type(data[field]).__name__ if field in data else "missing"


def enum_field(data: dict, field: str, allowed: tuple[str, ...]) -> str:
    if field not in data:
        return "missing"
    value = data[field]
    if isinstance(value, str) and value in allowed:
        return value
    return f"other_{type(value).__name__}"


def response_summary(response: httpx.Response) -> dict:
    summary = {"http_status": response.status_code}
    try:
        body = response.json()
    except ValueError:
        return {**summary, "format": "non_json"}

    summary.update(format="json", root_type=type(body).__name__)
    if not isinstance(body, dict):
        return summary
    summary["envelope"] = "scamalytics" if "scamalytics" in body else "root"
    data = body.get("scamalytics", body)
    summary["payload_type"] = type(data).__name__
    if not isinstance(data, dict):
        return summary
    summary.update(
        status=enum_field(data, "status", ("ok", "error")),
        mode=enum_field(data, "mode", ("test", "live")),
        credits_type=field_type(data, "credits"),
    )
    credits = data.get("credits")
    if isinstance(credits, dict):
        summary["credit_field_types"] = {
            field: field_type(credits, field)
            for field in ("remaining", "used", "last_sync_timestamp_utc")
        }
    try:
        ProbeResult.model_validate(data)
    except ValidationError as exc:
        # Model field paths and error types are fixed by ProbeResult's schema.
        # Never include validation inputs, messages, or arbitrary response fields.
        summary["validation_errors"] = [
            {"field": ".".join(map(str, error["loc"])), "type": error["type"]}
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
    return summary


async def diagnose(pool: KeyPool) -> dict:
    summaries = []

    async def inspect_response(response: httpx.Response) -> None:
        await response.aread()
        summaries.append(response_summary(response))

    # The CLI owns this shared client and inspects accounts sequentially.
    hooks = pool.client.http.event_hooks["response"]
    hooks.append(inspect_response)
    reports = []
    try:
        for key in pool.keys:
            status, lease = await pool.try_reserve(key, probe=True)
            report = {
                "id": key.id,
                "node": key.node,
                "api_host": getattr(key, "api_host", None)
                or ("api12" if key.node == "eu" else "api11"),
                "reservation": status,
                "probe_sent": False,
            }
            reports.append(report)
            if status != "ok":
                continue
            credits, error = None, None
            previous_responses = len(summaries)
            try:
                report["probe_sent"] = True
                credits = await pool.client.probe(key)
                report["result"] = "ok"
            except UpstreamError as exc:
                error = exc
                report["result"] = exc.kind
            finally:
                await pool.finish(lease, credits=credits, error=error)
            if len(summaries) > previous_responses:
                report["response"] = summaries[-1]
        return {"accounts": reports, "keys": await pool.snapshots()}
    finally:
        hooks.remove(inspect_response)


async def main() -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        settings = Settings()
        keys = settings.load_keys()
    except (OSError, ValueError):
        print(json.dumps({"error": "invalid_configuration"}))
        return 1

    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as http:
            client = ScamalyticsClient(http, settings.upstream_timeout_seconds)
            pool = KeyPool(redis, keys, settings, client)
            report = await diagnose(pool)
        print(json.dumps(report, indent=2))
        return 0
    except RedisError:
        print(json.dumps({"error": "state_unavailable"}))
        return 1
    finally:
        await redis.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
