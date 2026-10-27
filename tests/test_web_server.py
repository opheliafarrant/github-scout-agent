from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from github_scout_agent.agent_loop import AgentRunResult
from github_scout_agent.budget import RequestBudget
from github_scout_agent.config import AppConfig
from github_scout_agent.events import EventRecorder
from github_scout_agent.web_server import _store, web_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(web_app)


def test_index_route(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "GitHub Scout Agent" in response.text
    assert "text/html" in response.headers["content-type"]


def test_config_route_unconfigured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_SCOUT_MODEL", raising=False)
    monkeypatch.delenv("GITHUB_SCOUT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_SCOUT_MODEL_API_KEY", raising=False)

    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is False
    assert data["has_key"] is False


def test_config_route_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SCOUT_MODEL", "mock-model")
    monkeypatch.setenv("GITHUB_SCOUT_MODEL_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("GITHUB_SCOUT_MODEL_API_KEY", "secret-key")

    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["model"] == "mock-model"
    assert data["base_url"] == "https://api.example.com"
    assert data["has_key"] is True


def test_run_route_unconfigured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_SCOUT_MODEL", raising=False)
    monkeypatch.delenv("GITHUB_SCOUT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_SCOUT_MODEL_API_KEY", raising=False)

    response = client.post("/api/run", json={"task": "Find agents"})
    assert response.status_code == 200
    assert "event: error" in response.text
    assert "not_configured" in response.text


def test_run_route_streaming_and_storage(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SCOUT_MODEL", "mock-model")
    monkeypatch.setenv("GITHUB_SCOUT_MODEL_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("GITHUB_SCOUT_MODEL_API_KEY", "secret-key")

    def mock_run_research_task(
        task: str,
        *,
        model: object,
        github: object,
        budget: RequestBudget,
        events: EventRecorder | None = None,
        require_readme_override: bool | None = None,
    ) -> AgentRunResult:
        if events:
            events.emit("task_started", input_length=len(task))
            events.emit("tool_call_requested", tool_name="search_public_repositories", tool_call_id="call_1")
            events.emit("tool_finished", tool_name="search_public_repositories", ok=True)
            events.emit("budget_updated", accepted_candidate_count=1, max_candidates=3, github_requests_used=1, max_github_requests=20, model_calls_used=1, max_model_calls=10)
        return AgentRunResult(
            status="completed",
            termination_reason="model_finalized",
            final_text="Found repository: test/repo",
            budget=budget.snapshot(),
        )

    with patch("github_scout_agent.web_server.run_research_task", side_effect=mock_run_research_task):
        with patch("github_scout_agent.web_server.OpenAICompatibleClient"):
            with patch("github_scout_agent.web_server.GitHubRestClient"):
                response = client.post(
                    "/api/run",
                    json={"task": "Search agents", "max_candidates": 3},
                )
                assert response.status_code == 200
                content = response.text

                assert "event: event" in content
                assert "task_started" in content
                assert "tool_call_requested" in content
                assert "event: done" in content
                assert "model_finalized" in content

    # Verify history route
    history_res = client.get("/api/history")
    assert history_res.status_code == 200
    history_items = history_res.json()
    assert len(history_items) >= 1
    latest_task = history_items[-1]
    assert latest_task["task"] == "Search agents"
    assert latest_task["status"] == "completed"

    # Verify report route
    report_res = client.get(f"/api/report/{latest_task['id']}")
    assert report_res.status_code == 200
    report_data = report_res.json()
    assert report_data["status"] == "completed"
    assert report_data["termination_reason"] == "model_finalized"


def test_report_route_not_found(client: TestClient) -> None:
    response = client.get("/api/report/nonexistent_id")
    assert response.status_code == 200
    assert response.json() == {"error": "not_found"}
