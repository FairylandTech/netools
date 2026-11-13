import json

import pytest

from netools.ip_info.config import IpregistryKeyConfig, load_ipregistry_keys
from tests.support import registry_key


@pytest.mark.parametrize("field", ["id", "account_id", "key"])
def test_duplicate_ipregistry_credentials_rejected(tmp_path, field):
    entries = [
        {
            "id": name,
            "account_id": f"account-{name}",
            "key": f"synthetic-{name}",
            "initial_credits": 100,
        }
        for name in ("one", "two")
    ]
    entries[1][field] = entries[0][field]
    path = tmp_path / "ipregistry.json"
    path.write_text(json.dumps(entries))
    with pytest.raises(ValueError):
        load_ipregistry_keys(path)


def test_ipregistry_rotation_preserves_account_ledger():
    original = registry_key()
    updated = original.model_copy(update={"id": "renamed", "key": "replacement"})
    assert original.account_hash == updated.account_hash
    assert "synthetic" not in repr(original)


@pytest.mark.parametrize("credits", [-1, "100", True, None, 10**13])
def test_ipregistry_budget_must_be_explicit_and_valid(credits):
    with pytest.raises(ValueError):
        IpregistryKeyConfig(
            id="one", account_id="account", key="synthetic", initial_credits=credits
        )


def test_ipregistry_config_disabled_or_redacted(tmp_path):
    assert load_ipregistry_keys(None) == []
    path = tmp_path / "registry.json"
    path.write_text('[{"key":"synthetic-secret"}]')
    with pytest.raises(ValueError) as raised:
        load_ipregistry_keys(path)
    assert "synthetic-secret" not in str(raised.value)
    path.write_text("[]")
    assert load_ipregistry_keys(path) == []


@pytest.mark.parametrize("secret", ["synthetic\nkey", "synthetic key", "synthetic-密钥"])
def test_invalid_header_credentials_fail_without_leaking(tmp_path, secret):
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps([{"id": "one", "account_id": "one", "key": secret, "initial_credits": 100}])
    )
    with pytest.raises(ValueError) as raised:
        load_ipregistry_keys(path)
    assert "synthetic" not in str(raised.value)
