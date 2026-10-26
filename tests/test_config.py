from __future__ import annotations

import pytest
from pydantic import ValidationError

from github_scout_agent.config import (
    ENV_MODEL,
    ENV_MODEL_API_KEY,
    ENV_MODEL_BASE_URL,
    AppConfig,
    load_config,
    model_configuration_status,
)


def test_empty_environment_is_safe_and_offline() -> None:
    config = load_config({})

    assert config.model_ready is False
    assert model_configuration_status(config) == "not_configured"
    assert config.model_api_key is None


def test_complete_model_configuration_is_detected_without_calling_api() -> None:
    config = load_config(
        {
            ENV_MODEL_BASE_URL: "https://model.example.test/v1",
            ENV_MODEL: "test-model",
            ENV_MODEL_API_KEY: "secret-value",
        }
    )

    assert config.model_ready is True
    assert model_configuration_status(config) == "ready"
    assert "secret-value" not in repr(config)


def test_invalid_model_base_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig(model_base_url="file:///secret", model="test-model")
