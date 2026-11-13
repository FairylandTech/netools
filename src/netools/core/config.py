from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings read from the environment and ``.env``.

    Provider key files are only referenced here; each provider package owns the
    schema and loader for its own credentials.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_api_token: SecretStr = Field(min_length=16)
    scamalytics_keys_file: Path = Path("config/scamalytics-keys.json")
    ipregistry_keys_file: Path = Path("config/ipregistry-keys.json")
    ipregistry_quota_safety_margin: int = Field(default=5, ge=0)
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
