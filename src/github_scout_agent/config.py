"""Minimal, offline configuration loading for the S3-01 foundation."""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator


ENV_MODEL_BASE_URL = "GITHUB_SCOUT_MODEL_BASE_URL"
ENV_MODEL = "GITHUB_SCOUT_MODEL"
ENV_MODEL_API_KEY = "GITHUB_SCOUT_MODEL_API_KEY"
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"


class AppConfig(BaseModel):
    """User configuration with secrets kept out of normal representations."""

    model_config = ConfigDict(extra="forbid")

    model_base_url: str | None = None
    model: str | None = None
    model_api_key: SecretStr | None = None
    github_token: SecretStr | None = None

    @field_validator("model_base_url")
    @classmethod
    def validate_model_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()
        local_http_hosts = {"localhost", "127.0.0.1", "::1"}
        local_http = parsed.scheme == "http" and hostname in local_http_hosts
        if (parsed.scheme != "https" and not local_http) or not parsed.netloc:
            raise ValueError("model_base_url must be HTTPS; HTTP is allowed only for localhost development")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or ".." in parsed.path:
            raise ValueError("model_base_url must not contain credentials, query, fragment, or traversal")
        return value.rstrip("/")

    @property
    def model_ready(self) -> bool:
        """Return whether the minimum model configuration is present."""

        return bool(self.model_base_url and self.model and self.model_api_key)


def _optional_value(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def load_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Load configuration from environment variables without making network calls."""

    source = os.environ if environ is None else environ
    model_api_key = _optional_value(source, ENV_MODEL_API_KEY)
    github_token = _optional_value(source, ENV_GITHUB_TOKEN)
    return AppConfig(
        model_base_url=_optional_value(source, ENV_MODEL_BASE_URL),
        model=_optional_value(source, ENV_MODEL),
        model_api_key=SecretStr(model_api_key) if model_api_key else None,
        github_token=SecretStr(github_token) if github_token else None,
    )


def model_configuration_status(config: AppConfig) -> str:
    """Return a non-secret status suitable for CLI output."""

    if config.model_ready:
        return "ready"
    if config.model_api_key or config.model_base_url or config.model:
        return "incomplete"
    return "not_configured"
