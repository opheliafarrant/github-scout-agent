from __future__ import annotations

from typer.testing import CliRunner

from github_scout_agent import __version__
from github_scout_agent.cli import app


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
