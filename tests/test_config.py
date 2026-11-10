import pytest

from netools.config import KeyConfig


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
    key = KeyConfig(
        id="test", username="synthetic-user", key="synthetic-key", node=node, api_host=api_host
    )
    assert key.endpoint == f"https://{expected_host}.scamalytics.com/v3/synthetic-user/"


@pytest.mark.parametrize(
    "api_host",
    ["api14", "example.com", "api13.scamalytics.com", "https://api13.scamalytics.com", "127.0.0.1"],
)
def test_arbitrary_upstream_host_rejected(api_host):
    with pytest.raises(ValueError):
        KeyConfig(id="test", username="synthetic-user", key="synthetic-key", api_host=api_host)
