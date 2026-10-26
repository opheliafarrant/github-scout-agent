from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from github_scout_agent.cli import app
from github_scout_agent.gate_a import GateAConfig, run_gate_a_dry_run


def test_gate_a_valid_dry_run_passes_with_zero_github_requests() -> None:
    result = run_gate_a_dry_run()

    assert result.status == "passed"
    assert result.code == "ok"
    assert result.real_network_requests == 0
    assert result.github_requests == 0
    assert result.budget["model_calls_used"] == 1
    assert result.request_summary["request_payload"]["model"] == "deepseek-flash"
    assert result.request_summary["request_payload"]["path"] == "/chat/completions"
    assert result.request_summary["request_payload"]["thinking"] == {"type": "disabled"}
    assert result.request_summary["request_payload"]["tool_choice"] == "auto"
    assert result.request_summary["request_payload"]["stream"] is False
    assert result.request_summary["request_payload"]["max_tokens"] == 512
    assert result.request_summary["request_payload"]["tool_count"] == 1
    assert result.request_summary["request_payload"]["strict_present"] is False
    assert result.request_summary["request_payload"]["authorization_present"] is True
    assert "dry-run-placeholder" not in result.model_dump_json()


@pytest.mark.parametrize(
    "scenario",
    ["final_text", "unknown_tool", "invalid_json", "invalid_schema", "missing_id", "duplicate_id", "multiple_calls"],
)
def test_gate_a_protocol_failures_do_not_execute_tools(scenario: str) -> None:
    result = run_gate_a_dry_run(scenario)

    assert result.status == "failed"
    assert result.real_network_requests == 0
    assert result.github_requests == 0
    assert result.budget["model_calls_used"] == 1


@pytest.mark.parametrize(
    "scenario",
    ["http_400", "http_401", "http_403", "http_429", "http_500", "unsupported_tools", "timeout", "response_too_large"],
)
def test_gate_a_transport_failures_are_structured_and_not_retried(scenario: str) -> None:
    result = run_gate_a_dry_run(scenario)

    assert result.status == "failed"
    assert result.real_network_requests == 0
    assert result.github_requests == 0
    assert result.request_summary["model_requests"] == 1


def test_gate_a_missing_usage_is_unknown_not_fabricated() -> None:
    result = run_gate_a_dry_run("usage_missing")

    assert result.status == "passed"
    assert result.usage_summary == {"status": "unknown"}


def test_gate_a_configuration_mismatch_fails_before_transport() -> None:
    result = run_gate_a_dry_run(config=GateAConfig(strict_tools=True))

    assert result.status == "failed"
    assert result.code == "config_mismatch"
    assert result.request_summary["model_requests"] == 0
    assert result.real_network_requests == 0


def test_gate_a_result_is_json_safe_and_contains_no_secret() -> None:
    result = run_gate_a_dry_run()

    encoded = json.dumps(result.model_dump(), ensure_ascii=False)
    assert "dry-run-placeholder" not in encoded
    assert "Authorization" not in encoded


def test_gate_a_cli_requires_explicit_dry_run_or_online_confirmation() -> None:
    runner = CliRunner()

    no_mode = runner.invoke(app, ["gate-a"])
    no_confirmation = runner.invoke(app, ["gate-a", "--online"])
    conflicting = runner.invoke(app, ["gate-a", "--dry-run", "--online", "--confirm-live"])

    assert no_mode.exit_code == 2
    assert no_confirmation.exit_code == 2
    assert conflicting.exit_code == 2
