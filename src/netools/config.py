import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class KeyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    username: str = Field(pattern=r"^[a-zA-Z0-9_.@-]{1,128}$")
    key: SecretStr = Field(min_length=1)
    node: Literal["eu", "us"] = "eu"
    max_concurrency: int = Field(default=32, ge=1, le=1024)
    requests_per_second: float = Field(default=20, gt=0, le=10000)

    @property
    def endpoint(self) -> str:
        host = "api12" if self.node == "eu" else "api11"
        return f"https://{host}.scamalytics.com/v3/{self.username}"

    @property
    def account_hash(self) -> str:
        # Rotation must not reset an account's local quota estimate.
        return hashlib.sha256(f"{self.node}:{self.username}".encode()).hexdigest()[:24]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_api_token: SecretStr = Field(min_length=16)
    scamalytics_keys_file: Path = Path("config/keys.json")
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    redis_prefix: str = Field(default="netools", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    cache_ttl_seconds: int = Field(default=300, ge=1, le=86400)
    quota_refresh_seconds: int = Field(default=86400, ge=1)
    quota_safety_margin: int = Field(default=5, ge=0)
    max_probes_per_month: int = Field(default=60, ge=1, le=250)
    max_pending_lookups: int = Field(default=512, ge=1)
    http_max_connections: int = Field(default=256, ge=1)
    lookup_timeout_seconds: float = Field(default=15, gt=0)
    upstream_timeout_seconds: float = Field(default=5, gt=0)
    key_wait_seconds: float = Field(default=3, gt=0)
    lease_seconds: int = Field(default=30, ge=1)
    max_attempts: int = Field(default=3, ge=1, le=10)
    batch_workers: int = Field(default=32, ge=1, le=256)

    @model_validator(mode="after")
    def validate_timeouts(self) -> "Settings":
        if self.lease_seconds <= self.lookup_timeout_seconds + 2:
            raise ValueError("LEASE_SECONDS must exceed LOOKUP_TIMEOUT_SECONDS by more than 2")
        if self.upstream_timeout_seconds >= self.lookup_timeout_seconds:
            raise ValueError("UPSTREAM_TIMEOUT_SECONDS must be less than LOOKUP_TIMEOUT_SECONDS")
        return self

    def load_keys(self) -> list[KeyConfig]:
        try:
            keys = TypeAdapter(list[KeyConfig]).validate_json(
                self.scamalytics_keys_file.read_bytes()
            )
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
