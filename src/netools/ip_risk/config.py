import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter


class ScamalyticsKeyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    username: str = Field(pattern=r"^[a-zA-Z0-9_.@-]{1,128}$")
    key: SecretStr = Field(min_length=1)
    node: Literal["eu", "us"] = "eu"
    api_host: Literal["api11", "api12", "api13"] | None = None
    max_concurrency: int = Field(default=32, ge=1, le=1024)
    requests_per_second: float = Field(default=20, gt=0, le=10000)

    @property
    def endpoint(self) -> str:
        host = self.api_host or ("api12" if self.node == "eu" else "api11")
        return f"https://{host}.scamalytics.com/v3/{self.username}/"

    @property
    def account_hash(self) -> str:
        # Key rotation and api_host corrections retain the existing account ledger.
        return hashlib.sha256(f"{self.node}:{self.username}".encode()).hexdigest()[:24]


def load_scamalytics_keys(path: Path) -> list[ScamalyticsKeyConfig]:
    try:
        keys = TypeAdapter(list[ScamalyticsKeyConfig]).validate_json(path.read_bytes())
    except (OSError, ValueError):
        # ValidationError's input can contain the original secret JSON.
        raise ValueError("Cannot load key pool: check the keys file and its schema") from None
    if not keys or len(keys) > 128:
        raise ValueError("The pool must contain between 1 and 128 accounts")
    if len({key.id for key in keys}) != len(keys):
        raise ValueError("Key ids must be unique")
    if len({key.username for key in keys}) != len(keys):
        raise ValueError(
            "Each username may appear only once: account quotas must not be duplicated"
        )
    return keys
