import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, field_validator


class IpregistryKeyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    account_id: str = Field(pattern=r"^[a-zA-Z0-9_.@-]{1,128}$")
    key: SecretStr = Field(min_length=1)
    initial_credits: int = Field(ge=0, le=10**12, strict=True)
    max_concurrency: int = Field(default=32, ge=1, le=1024)
    requests_per_second: float = Field(default=20, gt=0, le=10000)

    @field_validator("key")
    @classmethod
    def validate_header_key(cls, value: SecretStr) -> SecretStr:
        # Invalid header encodings must fail at sanitized configuration loading.
        if any(not 33 <= ord(char) <= 126 for char in value.get_secret_value()):
            raise ValueError("Ipregistry key must contain printable ASCII without whitespace")
        return value

    @property
    def account_hash(self) -> str:
        # Stable across key rotation and local display-name changes.
        return hashlib.sha256(self.account_id.encode()).hexdigest()[:24]


def load_ipregistry_keys(path: Path | None) -> list[IpregistryKeyConfig]:
    """Load the ipregistry pool; ``None`` or an empty array disables the provider."""
    if path is None:
        return []
    try:
        keys = TypeAdapter(list[IpregistryKeyConfig]).validate_json(path.read_bytes())
    except (OSError, ValueError):
        raise ValueError(
            "Cannot load ipregistry pool: check the keys file and its schema"
        ) from None
    if len(keys) > 128:
        raise ValueError("The ipregistry pool supports at most 128 accounts")
    if len({key.id for key in keys}) != len(keys):
        raise ValueError("Ipregistry key ids must be unique")
    if len({key.account_id for key in keys}) != len(keys):
        raise ValueError("Each ipregistry account_id may appear only once")
    if len({key.key.get_secret_value() for key in keys}) != len(keys):
        raise ValueError("Ipregistry API keys must not be duplicated")
    return keys
