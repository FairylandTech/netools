"""Route contract on a real Redis with only Scamalytics configured."""

import json

import httpx

from netools.app import create_app


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
            for path in ("/v1/ip/risk", "/v1/ip/info", "/v1/ip/lookup", "/v1/score"):
                unauthorized = await api.get(path, params={"ip": "8.8.8.8"})
                assert unauthorized.status_code == 401
                assert unauthorized.headers["WWW-Authenticate"] == "Bearer"
            invalid = await api.get("/v1/keys", headers={"Authorization": b"Bearer \xc3\xa9"})
            assert invalid.status_code == 401
            api.headers["Authorization"] = f"Bearer {settings.service_api_token.get_secret_value()}"
            assert (await api.get("/v1/ip/risk", params={"ip": "invalid"})).status_code == 422
            assert (await api.get("/v1/ip/risk", params={"ip": "fe80::1%eth0"})).status_code == 422
            assert (await api.post("/v1/ip/risk/batch", json={"ips": []})).status_code == 422
            assert (
                await api.post("/v1/ip/risk/batch", json={"ips": ["8.8.8.8"] * 1001})
            ).status_code == 422

            response = await api.get("/v1/ip/risk", params={"ip": "8.8.8.8"})
            assert response.status_code == 200
            assert response.json()["score"] == 23
            assert "ipregistry" not in response.json()

            batch = await api.post("/v1/ip/risk/batch", json={"ips": ["8.8.8.8", "::1"]})
            assert [item["ip"] for item in batch.json()["results"]] == ["8.8.8.8", "::1"]

            info = await api.get("/v1/ip/info", params={"ip": "8.8.8.8"})
            assert info.status_code == 503
            assert info.json()["error"]["code"] == "ipregistry_not_configured"
            info_batch = await api.post("/v1/ip/info/batch", json={"ips": ["8.8.8.8"]})
            assert info_batch.status_code == 200
            assert info_batch.json()["results"][0]["error"]["code"] == "ipregistry_not_configured"

            combined = await api.get("/v1/ip/lookup", params={"ip": "8.8.8.8"})
            assert combined.status_code == 200
            assert combined.json()["risk"]["cached"] is True
            assert combined.json()["info"] is None
            assert combined.json()["info_error"]["code"] == "ipregistry_not_configured"

            legacy = await api.get("/v1/score", params={"ip": "8.8.8.8"})
            assert legacy.status_code == 200
            assert legacy.json()["score"] == 23
            assert legacy.json()["ipregistry"] is None
            assert legacy.json()["ipregistry_error"]["code"] == "ipregistry_not_configured"
            legacy_batch = await api.post("/v1/scores", json={"ips": ["8.8.8.8"]})
            assert legacy_batch.json()["results"][0]["result"]["score"] == 23

            for path in ("/v1/ip/risk/keys", "/v1/keys"):
                status = await api.get(path)
                assert status.status_code == 200
                assert key.key.get_secret_value() not in status.text
                assert key.username not in status.text
            assert (await api.get("/v1/ip/info/keys")).json() == []
            assert (await api.get("/v1/keys")).json()["ipregistry_keys"] == []

            openapi = (await api.get("/openapi.json")).json()
            assert openapi["paths"]["/v1/score"]["get"]["deprecated"] is True
            assert "deprecated" not in openapi["paths"]["/v1/ip/risk"]["get"]
