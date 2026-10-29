from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from github_scout_agent.config import (
    AppConfig,
    ENV_MODEL,
    ENV_MODEL_API_KEY,
    ENV_MODEL_BASE_URL,
    ENV_SETTINGS_FILE,
    load_config,
    mask_secret,
    model_configuration_status,
    protect_secret,
    read_settings,
    unprotect_secret,
    write_settings,
)
from github_scout_agent.agent_models import AgentMessage
from github_scout_agent.model_client import ModelAdapterError, OpenAICompatibleClient
from github_scout_agent.web_server import web_app

from typing import Iterator

requires_dpapi = pytest.mark.skipif(
    sys.platform != "win32", reason="secret storage relies on Windows DPAPI"
)

MODEL_ENV_VARS = (ENV_MODEL_BASE_URL, ENV_MODEL, ENV_MODEL_API_KEY)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(web_app) as test_client:
        yield test_client


@pytest.fixture()
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    monkeypatch.setenv(ENV_SETTINGS_FILE, str(path))
    for name in MODEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return path


def test_missing_settings_file_reads_as_empty(tmp_path: Path) -> None:
    assert read_settings(tmp_path / "absent.json") == {}


def test_corrupted_settings_file_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert read_settings(path) == {}


def test_non_object_settings_file_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("[]", encoding="utf-8")
    assert read_settings(path) == {}


@requires_dpapi
def test_secret_is_not_stored_in_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    write_settings({"model_api_key": "sk-plain-secret-value"}, settings_path=path)

    raw = path.read_text(encoding="utf-8")
    assert "sk-plain-secret-value" not in raw

    stored = json.loads(raw)["model_api_key"]
    assert stored.startswith("dpapi:")
    assert unprotect_secret(stored) == "sk-plain-secret-value"


@requires_dpapi
def test_settings_round_trip_preserves_public_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    write_settings(
        {
            "model_base_url": "https://api.example.com/v1",
            "model": "example-model",
            "model_api_key": "secret",
            "thinking_mode": "enabled",
            "strict_tools": False,
            "max_tokens": 4096,
        },
        settings_path=path,
    )

    values = read_settings(path)
    assert values["model_base_url"] == "https://api.example.com/v1"
    assert values["model"] == "example-model"
    assert values["thinking_mode"] == "enabled"
    assert values["strict_tools"] is False
    assert values["max_tokens"] == 4096


@requires_dpapi
def test_load_config_falls_back_to_settings_file(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_settings(
        {
            "model_base_url": "https://api.example.com/v1",
            "model": "from-file",
            "model_api_key": "file-key",
        },
        settings_path=settings_file,
    )

    config = load_config()

    assert config.model == "from-file"
    assert config.model_ready is True


@requires_dpapi
def test_environment_overrides_settings_file(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_settings(
        {
            "model_base_url": "https://api.example.com/v1",
            "model": "from-file",
            "model_api_key": "file-key",
        },
        settings_path=settings_file,
    )
    monkeypatch.setenv(ENV_MODEL, "from-env")

    config = load_config()

    assert config.model == "from-env"
    assert config.model_base_url == "https://api.example.com/v1"


@requires_dpapi
def test_explicit_environment_ignores_settings_file(settings_file: Path) -> None:
    write_settings(
        {
            "model_base_url": "https://api.example.com/v1",
            "model": "from-file",
            "model_api_key": "file-key",
        },
        settings_path=settings_file,
    )

    config = load_config({})

    assert config.model_ready is False
    assert model_configuration_status(config) == "not_configured"


def test_mask_secret_hides_middle_characters() -> None:
    assert mask_secret("sk-abcdefghijklmnop") == "sk-…mnop"
    assert mask_secret("short") == "…"
    assert mask_secret(None) is None


@requires_dpapi
def test_settings_route_never_returns_the_plaintext_key(settings_file: Path) -> None:
    write_settings(
        {
            "model_base_url": "https://api.example.com/v1",
            "model": "example-model",
            "model_api_key": "sk-super-secret",
        },
        settings_path=settings_file,
    )

    response = TestClient(web_app).get("/api/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_key"] is True
    assert payload["key_hint"] == "sk-…cret"
    assert "sk-super-secret" not in response.text


def test_settings_route_reports_missing_configuration(settings_file: Path) -> None:
    response = TestClient(web_app).get("/api/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is False
    assert payload["status"] == "not_configured"
    assert payload["has_key"] is False


@requires_dpapi
def test_update_settings_persists_and_becomes_ready(settings_file: Path) -> None:
    client = TestClient(web_app)

    response = client.put(
        "/api/settings",
        json={
            "model_base_url": "https://api.example.com/v1",
            "model": "example-model",
            "model_api_key": "sk-super-secret",
        },
    )

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert read_settings(settings_file)["model"] == "example-model"
    assert "sk-super-secret" not in response.text


@requires_dpapi
def test_update_settings_keeps_existing_key_when_blank(settings_file: Path) -> None:
    client = TestClient(web_app)
    client.put(
        "/api/settings",
        json={
            "model_base_url": "https://api.example.com/v1",
            "model": "example-model",
            "model_api_key": "sk-original",
        },
    )

    response = client.put(
        "/api/settings",
        json={"model": "renamed-model", "model_api_key": ""},
    )

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert read_settings(settings_file)["model"] == "renamed-model"
    assert unprotect_secret(read_settings(settings_file)["model_api_key"]) == "sk-original"


def test_update_settings_rejects_plain_http_remote_host(settings_file: Path) -> None:
    client = TestClient(web_app)

    response = client.put(
        "/api/settings",
        json={"model_base_url": "http://example.com/v1", "model": "m", "model_api_key": "k"},
    )

    assert response.status_code == 422
    assert "https" in response.json()["detail"].lower()
    assert read_settings(settings_file) == {}


def test_update_settings_rejects_file_scheme(settings_file: Path) -> None:
    client = TestClient(web_app)

    response = client.put(
        "/api/settings",
        json={"model_base_url": "file:///etc/passwd", "model": "m"},
    )

    assert response.status_code == 422


@requires_dpapi
def test_update_settings_allows_local_http_endpoint(settings_file: Path) -> None:
    client = TestClient(web_app)

    response = client.put(
        "/api/settings",
        json={
            "model_base_url": "http://127.0.0.1:11434/v1",
            "model": "qwen2.5:7b",
            "model_api_key": "local",
        },
    )

    assert response.status_code == 200
    assert response.json()["base_url"] == "http://127.0.0.1:11434/v1"


def test_validate_model_base_url_still_rejects_remote_http() -> None:
    with pytest.raises(Exception):
        AppConfig(model_base_url="http://api.example.com/v1", model="m", model_api_key="k")


LOOPBACK_BASE_URL = "http://127.0.0.1:11434/v1"


def test_loopback_endpoint_is_ready_without_api_key() -> None:
    config = AppConfig(model_base_url=LOOPBACK_BASE_URL, model="qwen2.5:7b")

    assert config.model_ready is True
    assert model_configuration_status(config) == "ready"


def test_remote_endpoint_still_requires_an_api_key() -> None:
    config = AppConfig(model_base_url="https://api.deepseek.com/v1", model="deepseek-chat")

    assert config.model_ready is False
    assert model_configuration_status(config) == "incomplete"


def test_settings_route_reports_loopback_endpoint_as_ready(
    client: TestClient, settings_file: Path
) -> None:
    response = client.put(
        "/api/settings",
        json={"model_base_url": LOOPBACK_BASE_URL, "model": "qwen2.5:7b"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["has_key"] is False
    assert read_settings(settings_file)["model"] == "qwen2.5:7b"


def test_keyless_loopback_client_sends_no_authorization_header() -> None:
    requests: list[httpx.Request] = []

    def dispatch(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]},
            request=request,
        )

    client = OpenAICompatibleClient(
        base_url=LOOPBACK_BASE_URL,
        api_key=None,
        model="qwen2.5:7b",
        transport=httpx.MockTransport(dispatch),
    )
    with client:
        client.complete([AgentMessage(role="user", content="hi")], [])

    assert len(requests) == 1
    assert "authorization" not in requests[0].headers
    assert requests[0].url.host == "127.0.0.1"


def test_remote_client_still_rejects_a_missing_key() -> None:
    with pytest.raises(ModelAdapterError) as excinfo:
        OpenAICompatibleClient(
            base_url="https://api.deepseek.com/v1", api_key=None, model="deepseek-chat"
        )

    assert excinfo.value.code == "model_not_configured"
