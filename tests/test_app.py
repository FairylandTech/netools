import json

import httpx
import pytest

from netools.app import create_app
from netools.config import Settings


async def test_api_auth_validation_batch_and_redaction(harness_factory, settings, monkeypatch):
    harness = harness_factory()
    key = harness.keys[0]
    settings.scamalytics_keys_file.write_text(
        json.dumps([{"id": key.id, "username": key.username, "key": key.key.get_secret_value()}])
    )
    monkeypatch.setattr("netools.app.ScamalyticsClient", lambda *args: harness.client)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:
            assert (await api.get("/healthz")).status_code == 200
            assert (await api.get("/readyz")).status_code == 200
            assert (await api.get("/v1/score", params={"ip": "8.8.8.8"})).status_code == 401
            invalid = await api.get("/v1/keys", headers={"Authorization": b"Bearer \xc3\xa9"})
            assert invalid.status_code == 401
            api.headers["Authorization"] = f"Bearer {settings.service_api_token.get_secret_value()}"
            assert (await api.get("/v1/score", params={"ip": "invalid"})).status_code == 422
            assert (await api.get("/v1/score", params={"ip": "fe80::1%eth0"})).status_code == 422
            assert (await api.post("/v1/scores", json={"ips": []})).status_code == 422
            assert (
                await api.post("/v1/scores", json={"ips": ["8.8.8.8"] * 1001})
            ).status_code == 422
            response = await api.get("/v1/score", params={"ip": "8.8.8.8"})
            assert response.status_code == 200
            assert response.json()["score"] == 23
            batch = await api.post("/v1/scores", json={"ips": ["8.8.8.8", "::1"]})
            assert [item["ip"] for item in batch.json()["results"]] == ["8.8.8.8", "::1"]
            status = await api.get("/v1/keys")
            assert status.status_code == 200
            assert key.key.get_secret_value() not in status.text
            assert key.username not in status.text


def test_invalid_config_does_not_expose_secrets(settings):
    secret = "synthetic-sensitive-value"
    settings.scamalytics_keys_file.write_text(f'{{"secret":"{secret}"}}')
    with pytest.raises(ValueError) as raised:
        settings.load_keys()
    assert secret not in str(raised.value)


def test_duplicate_accounts_rejected(settings):
    settings.scamalytics_keys_file.write_text(
        json.dumps(
            [
                {"id": "one", "username": "same", "key": "synthetic-one"},
                {"id": "two", "username": "same", "key": "synthetic-two", "node": "us"},
            ]
        )
    )
    with pytest.raises(ValueError, match="username"):
        settings.load_keys()


def test_unsafe_lease_configuration_rejected():
    with pytest.raises(ValueError, match="LEASE_SECONDS"):
        Settings(_env_file=None, service_api_token="synthetic-service-token", lease_seconds=10)


def test_service_token_must_be_explicitly_configured(monkeypatch):
    monkeypatch.delenv("SERVICE_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="service_api_token"):
        Settings(_env_file=None)
