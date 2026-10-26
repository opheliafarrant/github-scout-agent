from __future__ import annotations

from github_scout_agent.gate_b import run_gate_b_dry_run


def test_gate_b_dry_run_executes_three_model_rounds_and_four_mock_gets() -> None:
    result = run_gate_b_dry_run()

    assert result.status == "completed"
    assert result.termination_reason == "model_completed"
    assert result.model_rounds == 3
    assert result.github_requests == 4
    assert result.candidate_count == 3
    assert result.real_network_requests == 0
    requested = [item for item in result.tool_trace if item["event_type"] == "tool_call_requested"]
    finished = [item for item in result.tool_trace if item["event_type"] == "tool_finished"]
    assert [item["tool_name"] for item in requested] == [
        "search_public_repositories",
        "search_public_repositories",
        "get_public_repository",
        "get_public_readme",
    ]
    assert len(finished) == 4
    assert all(item["ok"] is True for item in finished)


def test_gate_b_dry_run_preserves_budget_and_network_isolation() -> None:
    result = run_gate_b_dry_run()

    assert result.budget["model_calls_used"] == 3
    assert result.budget["github_requests_used"] == 4
    assert result.budget["accepted_candidate_count"] == 3
    assert result.budget["fatal_exhausted_dimensions"] == ()
    assert result.real_network_requests == 0
