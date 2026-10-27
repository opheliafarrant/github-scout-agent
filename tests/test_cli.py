from __future__ import annotations

import json

import httpx
from pydantic import SecretStr
from typer.testing import CliRunner

from github_scout_agent import __version__
from github_scout_agent.config import AppConfig
from github_scout_agent.cli import app
from github_scout_agent.model_client import ModelAdapterError, OpenAICompatibleClient
from github_scout_agent.github_api import GitHubRestClient


runner = CliRunner()


def test_help_command_is_available() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "github-scout-agent" in result.stdout
    assert "version" in result.stdout


def test_version_command_reports_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_missing_api_key_is_reported_without_network_call(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_SCOUT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_SCOUT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_SCOUT_MODEL", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    assert "Model API Key is not configured" in result.stdout
    assert "no model call will be attempted" in result.stdout
    assert "GITHUB_TOKEN" not in result.stdout


def test_mock_demo_runs_without_credentials_or_network(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_SCOUT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_SCOUT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_SCOUT_MODEL", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    result = runner.invoke(app, ["mock-demo"])

    assert result.exit_code == 0
    assert "tool_call_requested" in result.stdout
    assert "status=completed" in result.stdout
    assert "api_key" not in result.stdout.lower()


def test_run_requires_explicit_confirmation(monkeypatch) -> None:
    result = runner.invoke(app, ["run", "find public repositories"])

    assert result.exit_code == 2
    assert "no request was made" in result.stderr


def test_run_classifies_validation_and_value_errors_without_raw_details(monkeypatch) -> None:
    def invalid_config() -> AppConfig:
        return AppConfig(model_base_url="file:///secret", model="model")

    monkeypatch.setattr("github_scout_agent.cli.load_config", invalid_config)
    validation = runner.invoke(app, ["run", "find x", "--confirm-live"])

    assert validation.exit_code == 1
    assert "validation_error" in validation.stderr
    assert "file:///secret" not in validation.stderr

    def invalid_value() -> AppConfig:
        raise ValueError("sensitive internal detail")

    monkeypatch.setattr("github_scout_agent.cli.load_config", invalid_value)
    value = runner.invoke(app, ["run", "find x", "--confirm-live"])

    assert value.exit_code == 1
    assert "value_error" in value.stderr
    assert "sensitive internal detail" not in value.stderr


def test_run_preserves_structured_model_error_code(monkeypatch) -> None:
    def invalid_model() -> AppConfig:
        raise ModelAdapterError("hidden detail", code="network_error", category="network")

    monkeypatch.setattr("github_scout_agent.cli.load_config", invalid_model)
    result = runner.invoke(app, ["run", "find x", "--confirm-live"])

    assert result.exit_code == 1
    assert "network_error" in result.stderr
    assert "hidden detail" not in result.stderr


def test_run_production_assembly_uses_mock_transports(monkeypatch) -> None:
    model_calls = 0

    def model_handler(request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "cli-search",
                    "type": "function",
                    "function": {
                        "name": "search_public_repositories",
                        "arguments": json.dumps({"query": "x"}),
                    },
                }],
            }
            return httpx.Response(
                200,
                json={"choices": [{"message": message, "finish_reason": "tool_calls"}]},
                request=request,
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "Evidence reviewed."}, "finish_reason": "stop"}]},
            request=request,
        )

    def github_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [{
                    "id": 1,
                    "full_name": "octo/cli-demo",
                    "html_url": "https://github.com/octo/cli-demo",
                    "private": False,
                    "visibility": "public",
                    "fork": False,
                    "archived": False,
                    "disabled": False,
                    "stargazers_count": 1,
                    "forks_count": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "pushed_at": "2024-01-01T00:00:00Z",
                    "language": "Python",
                    "license": None,
                    "topics": [],
                }],
            },
            request=request,
        )

    class MockGitHub(GitHubRestClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(github_handler)
            super().__init__(*args, **kwargs)

    class MockModel(OpenAICompatibleClient):
        @classmethod
        def from_config(cls, config, *, budget=None, transport=None, timeout=10.0):
            return cls(
                base_url=config.model_base_url,
                api_key=config.model_api_key,
                model=config.model,
                budget=budget,
                transport=httpx.MockTransport(model_handler),
                timeout=timeout,
                thinking_mode=config.thinking_mode,
                strict_tools=config.strict_tools,
                max_tokens=config.max_tokens,
            )

    monkeypatch.setattr("github_scout_agent.cli.load_config", lambda: AppConfig(
        model_base_url="https://model.example.test/v1",
        model="mock-model",
        model_api_key=SecretStr("mock-key"),
        thinking_mode="disabled",
        strict_tools=False,
        max_tokens=512,
    ))
    monkeypatch.setattr("github_scout_agent.github_api.GitHubRestClient", MockGitHub)
    monkeypatch.setattr("github_scout_agent.model_client.OpenAICompatibleClient", MockModel)

    result = runner.invoke(app, ["run", "find x", "--confirm-live"])

    assert result.exit_code == 0
    assert "Status: completed" in result.stdout
    assert "octo/cli-demo" in result.stdout
    assert "Evidence reviewed." in result.stdout
    assert model_calls == 2


def test_run_configurable_budgets(monkeypatch) -> None:
    captured_budget = None

    def fake_run_research_task(task, *, model, github, budget):
        nonlocal captured_budget
        captured_budget = budget
        from github_scout_agent.agent_loop import AgentRunResult

        return AgentRunResult(
            status="completed",
            termination_reason="model_completed",
            final_text="done",
            messages=[],
            tool_results=[],
            events=[],
            budget=budget.snapshot(),
            evidence_complete=True,
            evidence_gaps=[],
        )

    class DummyCtx:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr("github_scout_agent.cli.load_config", lambda: AppConfig(
        model_base_url="https://model.example.test/v1",
        model="mock-model",
        model_api_key=SecretStr("mock-key"),
        thinking_mode="disabled",
        strict_tools=False,
        max_tokens=512,
    ))
    monkeypatch.setattr("github_scout_agent.application.run_research_task", fake_run_research_task)
    monkeypatch.setattr("github_scout_agent.github_api.GitHubRestClient", lambda *a, **kw: DummyCtx())
    monkeypatch.setattr("github_scout_agent.model_client.OpenAICompatibleClient.from_config", lambda config, **kw: DummyCtx())

    result = runner.invoke(app, [
        "run", "find x", "--confirm-live",
        "--max-model-calls", "12",
        "--max-github-requests", "25",
        "--max-candidates", "5",
    ])
    assert result.exit_code == 0
    assert captured_budget is not None
    assert captured_budget.max_model_calls == 12
    assert captured_budget.max_github_requests == 25
    assert captured_budget.max_candidates == 5

