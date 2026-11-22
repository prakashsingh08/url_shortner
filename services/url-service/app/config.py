"""Environment-based configuration (LLD §9.3). Nothing is read from files baked into the image."""

import sys
from typing import Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    service_name: str = "url-service"
    app_port: int = Field(default=8000, ge=1, le=65535)

    # Required. An empty value counts as missing.
    db_primary_url: str = Field(min_length=1)
    db_pool_size_write: int = Field(default=10, ge=1, le=100)
    readiness_db_timeout_ms: int = Field(default=500, ge=50)

    shutdown_drain_seconds: float = Field(default=2.0, ge=0)
    shutdown_timeout_seconds: int = Field(default=10, ge=1)
    max_body_bytes: int = Field(default=16 * 1024, ge=1)

    @property
    def test_routes_enabled(self) -> bool:
        return self.app_env == "test"


def describe_config_errors(exc: ValidationError) -> list[str]:
    """Turn a pydantic error into 'ENV_VAR: reason' strings that name the variable."""
    problems = []
    for err in exc.errors():
        var = str(err["loc"][0]).upper() if err["loc"] else "?"
        reason = "missing" if err["type"] in ("missing", "string_too_short") else err["msg"]
        problems.append(f"{var}: {reason}")
    return problems


def load_settings_or_exit(log) -> Settings:
    """Load settings; on error log which variables are wrong and exit non-zero."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = describe_config_errors(exc)
        log.error(
            "Invalid configuration: " + "; ".join(problems),
            extra={"fields": {"configErrors": problems}},
        )
        sys.exit(2)
