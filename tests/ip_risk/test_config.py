import json

import pytest

from netools.ip_risk.config import ScamalyticsKeyConfig, load_scamalytics_keys


@pytest.mark.parametrize(
    ("node", "api_host", "expected_host"),
    [
        ("eu", None, "api12"),
        ("us", None, "api11"),
        ("us", "api13", "api13"),
        ("eu", "api13", "api13"),
        ("eu", "api11", "api11"),
        ("us", "api12", "api12"),
    ],
)
def test_assigned_host_overrides_legacy_node(node, api_host, expected_host):
    key = ScamalyticsKeyConfig(
        id="test", username="synthetic-user", key="synthetic-key", node=node, api_host=api_host
    )
    assert key.endpoint == f"https://{expected_host}.scamalytics.com/v3/synthetic-user/"


@pytest.mark.parametrize(
    "api_host",
    ["api14", "example.com", "api13.scamalytics.com", "https://api13.scamalytics.com", "127.0.0.1"],
)
def test_arbitrary_upstream_host_rejected(api_host):
    with pytest.raises(ValueError):
        ScamalyticsKeyConfig(
            id="test", username="synthetic-user", key="synthetic-key", api_host=api_host
        )


def test_invalid_config_does_not_expose_secrets(settings):
    secret = "synthetic-sensitive-value"
    settings.scamalytics_keys_file.write_text(f'{{"secret":"{secret}"}}')
    with pytest.raises(ValueError) as raised:
        load_scamalytics_keys(settings.scamalytics_keys_file)
    assert secret not in str(raised.value)


def test_empty_pool_rejected(settings):
    settings.scamalytics_keys_file.write_text("[]")
    with pytest.raises(ValueError, match="between 1 and 128"):
        load_scamalytics_keys(settings.scamalytics_keys_file)


@pytest.mark.parametrize("second_api_host", [None, "api13"])
def test_duplicate_accounts_rejected(settings, second_api_host):
    settings.scamalytics_keys_file.write_text(
        json.dumps(
            [
                {"id": "one", "username": "same", "key": "synthetic-one"},
                {
                    "id": "two",
                    "username": "same",
                    "key": "synthetic-two",
                    "node": "us",
                    "api_host": second_api_host,
                },
            ]
        )
    )
    with pytest.raises(ValueError, match="username"):
        load_scamalytics_keys(settings.scamalytics_keys_file)
