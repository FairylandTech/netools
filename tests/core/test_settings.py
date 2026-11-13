import pytest

from netools.core.config import Settings


def test_unsafe_lease_configuration_rejected():
    with pytest.raises(ValueError, match="LEASE_SECONDS"):
        Settings(_env_file=None, service_api_token="synthetic-service-token", lease_seconds=10)


def test_upstream_timeout_must_fit_within_lookup_timeout():
    with pytest.raises(ValueError, match="UPSTREAM_TIMEOUT_SECONDS"):
        Settings(
            _env_file=None,
            service_api_token="synthetic-service-token",
            upstream_timeout_seconds=15,
            lookup_timeout_seconds=15,
        )


def test_service_token_must_be_explicitly_configured(monkeypatch):
    monkeypatch.delenv("SERVICE_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="service_api_token"):
        Settings(_env_file=None)
