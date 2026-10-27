"""Minimal, offline configuration loading for the S3-01 foundation."""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlparse

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


ENV_MODEL_BASE_URL = "GITHUB_SCOUT_MODEL_BASE_URL"
ENV_MODEL = "GITHUB_SCOUT_MODEL"
ENV_MODEL_API_KEY = "GITHUB_SCOUT_MODEL_API_KEY"
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"
ENV_MODEL_THINKING_MODE = "GITHUB_SCOUT_MODEL_THINKING_MODE"
ENV_MODEL_STRICT_TOOLS = "GITHUB_SCOUT_MODEL_STRICT_TOOLS"
ENV_MODEL_MAX_TOKENS = "GITHUB_SCOUT_MODEL_MAX_TOKENS"


class AppConfig(BaseModel):
    """User configuration with secrets kept out of normal representations."""

    model_config = ConfigDict(extra="forbid")

    model_base_url: str | None = None
    model: str | None = None
    model_api_key: SecretStr | None = None
    github_token: SecretStr | None = None
    thinking_mode: Literal["disabled", "enabled"] | None = None
    strict_tools: bool = True
    max_tokens: int | None = Field(default=None, gt=0)

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


def _optional_bool(environ: Mapping[str, str], name: str) -> bool | None:
    value = _optional_value(environ, name)
    if value is None:
        return None
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _optional_int(environ: Mapping[str, str], name: str) -> int | None:
    value = _optional_value(environ, name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def load_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Load configuration from environment variables without making network calls."""

    source = os.environ if environ is None else environ
    model_api_key = _optional_value(source, ENV_MODEL_API_KEY)
    github_token = _optional_value(source, ENV_GITHUB_TOKEN)
    strict_tools = _optional_bool(source, ENV_MODEL_STRICT_TOOLS)
    return AppConfig(
        model_base_url=_optional_value(source, ENV_MODEL_BASE_URL),
        model=_optional_value(source, ENV_MODEL),
        model_api_key=SecretStr(model_api_key) if model_api_key else None,
        github_token=SecretStr(github_token) if github_token else None,
        thinking_mode=_validate_thinking_mode(_optional_value(source, ENV_MODEL_THINKING_MODE)),
        strict_tools=True if strict_tools is None else strict_tools,
        max_tokens=_optional_int(source, ENV_MODEL_MAX_TOKENS),
    )


def _validate_thinking_mode(value: str | None) -> Literal["disabled", "enabled"] | None:
    """Validate that thinking_mode is a recognized literal value."""

    if value is None:
        return None
    if value not in {"disabled", "enabled"}:
        raise ValueError(f"GITHUB_SCOUT_MODEL_THINKING_MODE must be 'disabled' or 'enabled', got '{value}'")
    return value  # type: ignore[return-value]


def model_configuration_status(config: AppConfig) -> str:
    """Return a non-secret status suitable for CLI output."""

    if config.model_ready:
        return "ready"
    if config.model_api_key or config.model_base_url or config.model:
        return "incomplete"
    return "not_configured"
