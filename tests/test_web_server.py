from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from github_scout_agent.agent_loop import AgentRunResult
from github_scout_agent.budget import RequestBudget
from github_scout_agent.config import AppConfig
from github_scout_agent.events import EventRecorder
from github_scout_agent.web_server import HistoryStore, TaskStatus, _store, web_app


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
    assert report_data["feedback_trace"] == []


def test_report_route_not_found(client: TestClient) -> None:
    response = client.get("/api/report/nonexistent_id")
    assert response.status_code == 200
    assert response.json() == {"error": "not_found"}


def test_history_store_persistence_and_reload(tmp_path: Path) -> None:
    history_file = tmp_path / "web_history.jsonl"
    store = HistoryStore(file_path=history_file, max_entries=50)

    # 1. Store initial tasks
    task1 = TaskStatus(
        id="task_1",
        task="Find RAG frameworks",
        status="completed",
        termination_reason="model_finalized",
        started_at=1700000000.0,
        report="# RAG Report\n\n- LangChain",
        budget={"model_calls": 3},
    )
    task2 = TaskStatus(
        id="task_2",
        task="Find Vector DBs",
        status="running",
        started_at=1700000010.0,
    )
    store.set(task1)
    store.set(task2)

    assert history_file.is_file()
    assert len(store) == 2

    # Update task2
    store.update(
        "task_2",
        status="completed",
        termination_reason="budget_exhausted",
        report="# Vector DB Report",
        budget={"model_calls": 10},
    )

    # 2. Simulate server restart by creating a new store instance pointing to same file
    store_reloaded = HistoryStore(file_path=history_file, max_entries=50)
    assert len(store_reloaded) == 2
    assert "task_1" in store_reloaded
    assert "task_2" in store_reloaded

    loaded_task2 = store_reloaded.get("task_2")
    assert loaded_task2 is not None
    assert loaded_task2.status == "completed"
    assert loaded_task2.termination_reason == "budget_exhausted"
    assert loaded_task2.report == "# Vector DB Report"
    assert loaded_task2.budget == {"model_calls": 10}


def test_history_store_capacity_fifo(tmp_path: Path) -> None:
    history_file = tmp_path / "fifo_history.jsonl"
    store = HistoryStore(file_path=history_file, max_entries=3)

    for i in range(5):
        store.set(TaskStatus(
            id=f"task_{i}",
            task=f"Task {i}",
            status="completed",
            started_at=float(i),
        ))

    assert len(store) == 3
    # 0 and 1 should have been evicted
    assert "task_0" not in store
    assert "task_1" not in store
    # 2, 3, 4 should remain
    assert "task_2" in store
    assert "task_3" in store
    assert "task_4" in store

    # Verify file reload preserves only the 3 remaining
    reloaded = HistoryStore(file_path=history_file, max_entries=3)
    assert len(reloaded) == 3
    assert [t.id for t in reloaded.values()] == ["task_2", "task_3", "task_4"]


def test_history_store_concurrency(tmp_path: Path) -> None:
    import concurrent.futures

    history_file = tmp_path / "concurrent_history.jsonl"
    store = HistoryStore(file_path=history_file, max_entries=100)

    def write_task(idx: int) -> None:
        tid = f"task_thread_{idx}"
        store.set(TaskStatus(
            id=tid,
            task=f"Thread task {idx}",
            status="running",
            started_at=float(idx),
        ))
        store.update(tid, status="completed", report=f"Report {idx}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(write_task, i) for i in range(40)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert len(store) == 40
    # Verify file integrity by reloading
    reloaded = HistoryStore(file_path=history_file, max_entries=100)
    assert len(reloaded) == 40
    for i in range(40):
        t = reloaded.get(f"task_thread_{i}")
        assert t is not None
        assert t.status == "completed"
        assert t.report == f"Report {i}"


def test_history_store_corrupted_lines_resilience(tmp_path: Path) -> None:
    history_file = tmp_path / "corrupt_history.jsonl"
    history_file.write_text(
        '{"id": "valid_1", "task": "T1", "status": "completed", "started_at": 1.0}\n'
        'NOT VALID JSON LINE\n'
        '{"partial": true}\n'
        '{"id": "valid_2", "task": "T2", "status": "completed", "started_at": 2.0}\n',
        encoding="utf-8",
    )
    store = HistoryStore(file_path=history_file, max_entries=10)
    assert len(store) == 2
    assert "valid_1" in store
    assert "valid_2" in store



def test_history_store_persists_the_feedback_trace(tmp_path: Path) -> None:
    history_file = tmp_path / "web_history.jsonl"
    store = HistoryStore(file_path=history_file, max_entries=10)
    store.set(
        TaskStatus(
            id="task_trace",
            task="Search agents",
            status="running",
            started_at=1700000000.0,
        )
    )
    trace = [
        {"index": 1, "source": "tool", "label": "get_public_readme", "bytes": 900, "used": 900},
        {"index": 2, "source": "model", "label": "assistant", "bytes": 120, "used": 1_020},
    ]
    store.update(
        "task_trace",
        status="completed",
        termination_reason="model_finalized",
        report="# report",
        feedback_trace=trace,
    )

    reloaded = HistoryStore(file_path=history_file, max_entries=10)
    stored = reloaded.get("task_trace")
    assert stored is not None
    assert stored.feedback_trace == trace
