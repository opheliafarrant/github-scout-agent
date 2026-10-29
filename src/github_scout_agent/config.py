"""Minimal, offline configuration loading for the S3-01 foundation."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


ENV_MODEL_BASE_URL = "GITHUB_SCOUT_MODEL_BASE_URL"
ENV_MODEL = "GITHUB_SCOUT_MODEL"
ENV_MODEL_API_KEY = "GITHUB_SCOUT_MODEL_API_KEY"
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"
ENV_MODEL_THINKING_MODE = "GITHUB_SCOUT_MODEL_THINKING_MODE"
ENV_MODEL_STRICT_TOOLS = "GITHUB_SCOUT_MODEL_STRICT_TOOLS"
ENV_MODEL_MAX_TOKENS = "GITHUB_SCOUT_MODEL_MAX_TOKENS"

ENV_SETTINGS_FILE = "GITHUB_SCOUT_SETTINGS_FILE"
ENV_HISTORY_FILE = "GITHUB_SCOUT_HISTORY_FILE"

APP_DIR_NAME = "GitHubScoutAgent"
SETTINGS_FILE_NAME = "config.json"
HISTORY_FILE_NAME = "web_history.jsonl"

SECRET_FIELDS = ("model_api_key", "github_token")
SETTINGS_FIELDS = (
    "model_base_url",
    "model",
    "model_api_key",
    "github_token",
    "thinking_mode",
    "strict_tools",
    "max_tokens",
)

SECRET_PREFIX = "dpapi:"

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback_endpoint(value: str | None) -> bool:
    """Return whether a URL points at this machine, where keyless local servers live."""

    if not value:
        return False
    return (urlparse(value).hostname or "").lower() in LOOPBACK_HOSTS


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
        local_http = parsed.scheme == "http" and hostname in LOOPBACK_HOSTS
        if (parsed.scheme != "https" and not local_http) or not parsed.netloc:
            raise ValueError("model_base_url must be HTTPS; HTTP is allowed only for localhost development")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or ".." in parsed.path:
            raise ValueError("model_base_url must not contain credentials, query, fragment, or traversal")
        return value.rstrip("/")

    @property
    def model_ready(self) -> bool:
        """Return whether the minimum model configuration is present."""

        return bool(
            self.model_base_url
            and self.model
            and (self.model_api_key or is_loopback_endpoint(self.model_base_url))
        )


def default_app_dir() -> Path:
    """Return the per-user application directory for settings and history."""

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


def default_settings_path() -> Path:
    """Return the settings file path, honouring an explicit override."""

    override = os.environ.get(ENV_SETTINGS_FILE)
    if override:
        return Path(override).expanduser()
    return default_app_dir() / SETTINGS_FILE_NAME


def default_history_path() -> Path:
    """Return the Web UI history file path, honouring an explicit override."""

    override = os.environ.get(ENV_HISTORY_FILE)
    if override:
        return Path(override).expanduser()
    return default_app_dir() / HISTORY_FILE_NAME


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_crypt(data: bytes, *, unprotect: bool) -> bytes:
    """Run a Windows DPAPI call bound to the current user profile."""

    if sys.platform != "win32":
        raise RuntimeError("DPAPI secret storage requires Windows")
    if not data:
        raise ValueError("DPAPI input must not be empty")

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer_in = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buffer_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    call = crypt32.CryptUnprotectData if unprotect else crypt32.CryptProtectData
    if not call(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI operation failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect_secret(value: str) -> str:
    """Encrypt a secret for storage using DPAPI."""

    encrypted = _dpapi_crypt(value.encode("utf-8"), unprotect=False)
    return SECRET_PREFIX + base64.b64encode(encrypted).decode("ascii")


def unprotect_secret(stored: str) -> str | None:
    """Decrypt a stored secret; return None when it cannot be recovered."""

    if not stored.startswith(SECRET_PREFIX):
        return stored or None
    try:
        raw = base64.b64decode(stored[len(SECRET_PREFIX) :], validate=True)
        return _dpapi_crypt(raw, unprotect=True).decode("utf-8")
    except (ValueError, OSError, RuntimeError):
        return None


def mask_secret(value: str | None) -> str | None:
    """Return a display-safe hint that never reveals the whole secret."""

    if not value:
        return None
    if len(value) <= 7:
        return "…"
    return f"{value[:3]}…{value[-4:]}"


def read_settings(settings_path: Path | str | None = None) -> dict[str, object]:
    """Read decrypted settings from disk; missing or corrupt files yield {}."""

    path = Path(settings_path) if settings_path is not None else default_settings_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}

    values: dict[str, object] = {}
    for field in SETTINGS_FIELDS:
        value = raw.get(field)
        if value is None or value == "":
            continue
        if field in SECRET_FIELDS:
            if not isinstance(value, str):
                continue
            decrypted = unprotect_secret(value)
            if decrypted:
                values[field] = decrypted
            continue
        values[field] = value
    return values


def write_settings(values: Mapping[str, object], settings_path: Path | str | None = None) -> Path:
    """Persist settings atomically, encrypting secret fields with DPAPI."""

    path = Path(settings_path) if settings_path is not None else default_settings_path()
    payload: dict[str, object] = {"version": 1}
    for field in SETTINGS_FIELDS:
        value = values.get(field)
        if value is None or value == "":
            continue
        if field in SECRET_FIELDS and isinstance(value, str):
            payload[field] = protect_secret(value)
        else:
            payload[field] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


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


def load_config(
    environ: Mapping[str, str] | None = None,
    *,
    settings_path: Path | str | None = None,
) -> AppConfig:
    """Load configuration without making network calls.

    Sources are merged per field: environment variables win over the settings
    file. Passing environ explicitly keeps the call hermetic unless
    settings_path is also given, so tests never read the user's real file.
    """

    explicit_environment = environ is not None
    source = os.environ if environ is None else environ
    if settings_path is None and not explicit_environment:
        settings_path = default_settings_path()
    stored = read_settings(settings_path) if settings_path is not None else {}

    def pick(env_name: str, field: str) -> str | None:
        value = _optional_value(source, env_name)
        if value is not None:
            return value
        stored_value = stored.get(field)
        if stored_value is None or stored_value == "":
            return None
        return str(stored_value)

    model_api_key = pick(ENV_MODEL_API_KEY, "model_api_key")
    github_token = pick(ENV_GITHUB_TOKEN, "github_token")

    strict_tools = _optional_bool(source, ENV_MODEL_STRICT_TOOLS)
    if strict_tools is None:
        stored_strict = stored.get("strict_tools")
        strict_tools = stored_strict if isinstance(stored_strict, bool) else None

    raw_max_tokens = _optional_value(source, ENV_MODEL_MAX_TOKENS)
    if raw_max_tokens is not None:
        max_tokens = _optional_int(source, ENV_MODEL_MAX_TOKENS)
    else:
        stored_max = stored.get("max_tokens")
        if isinstance(stored_max, bool) or stored_max is None or stored_max == "":
            max_tokens = None
        else:
            try:
                max_tokens = int(stored_max)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                max_tokens = None

    return AppConfig(
        model_base_url=pick(ENV_MODEL_BASE_URL, "model_base_url"),
        model=pick(ENV_MODEL, "model"),
        model_api_key=SecretStr(model_api_key) if model_api_key else None,
        github_token=SecretStr(github_token) if github_token else None,
        thinking_mode=_validate_thinking_mode(pick(ENV_MODEL_THINKING_MODE, "thinking_mode")),
        strict_tools=True if strict_tools is None else strict_tools,
        max_tokens=max_tokens,
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
