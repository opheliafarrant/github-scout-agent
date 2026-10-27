"""FastAPI web server with SSE streaming for the GitHub Scout Agent."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from .application import run_research_task, render_mvp_report
from .budget import RequestBudget
from .config import AppConfig, load_config, ENV_MODEL_BASE_URL, ENV_MODEL, ENV_MODEL_API_KEY, ENV_MODEL_THINKING_MODE, ENV_MODEL_STRICT_TOOLS
from .events import AgentEvent, EventRecorder
from .github_api import GitHubRestClient
from .model_client import OpenAICompatibleClient


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

web_app = FastAPI(title="GitHub Scout Agent", version="1.1.0")


class TaskRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=2000)
    max_candidates: int = Field(default=3, ge=1, le=10)
    max_model_calls: int = Field(default=10, ge=1, le=30)
    max_github_requests: int = Field(default=20, ge=1, le=60)
    max_task_seconds: int = Field(default=180, ge=10, le=600)


class TaskStatus(BaseModel):
    id: str
    task: str
    status: str
    termination_reason: str = ""
    started_at: float
    report: str = ""
    budget: dict = Field(default_factory=dict)


_store: dict[str, TaskStatus] = {}


def _make_config(overrides: dict[str, str] | None = None) -> AppConfig:
    import os
    env = dict(os.environ)
    if overrides:
        env.update(overrides)
    return load_config(env)


@web_app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_path = TEMPLATES_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@web_app.get("/api/config")
async def config_status() -> dict:
    config = load_config()
    return {
        "ready": config.model_ready,
        "model": config.model or "(not set)",
        "base_url": config.model_base_url or "(not set)",
        "has_key": config.model_api_key is not None,
        "thinking_mode": config.thinking_mode or "disabled",
    }


@web_app.post("/api/run")
async def run_task(req: TaskRequest, request: Request) -> StreamingResponse:
    task_id = f"task_{int(time.monotonic() * 1000)}"
    status = TaskStatus(id=task_id, task=req.task, status="running", started_at=time.monotonic())
    _store[task_id] = status

    return StreamingResponse(
        _run_and_stream(task_id, req, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_and_stream(task_id: str, req: TaskRequest, request: Request) -> AsyncGenerator[str, None]:
    config = load_config()
    if not config.model_ready:
        yield _sse("error", {"code": "not_configured", "message": "Model configuration incomplete. Set env vars and restart."})
        status = _store.get(task_id)
        if status:
            status.status = "error"
            status.termination_reason = "configuration"
        return

    budget = RequestBudget(
        max_model_calls=req.max_model_calls,
        max_github_requests=req.max_github_requests,
        max_candidates=req.max_candidates,
        max_task_seconds=req.max_task_seconds,
        max_feedback_bytes=64 * 1024,
        max_model_response_bytes=32 * 1024,
        max_response_bytes=256 * 1024,
    )

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    class SSEEventRecorder(EventRecorder):
        def emit(self, event_type: str, **details: object) -> AgentEvent:
            evt = super().emit(event_type, **details)
            loop.call_soon_threadsafe(event_queue.put_nowait, evt)
            return evt

    events = SSEEventRecorder()

    def run_agent_sync() -> None:
        try:
            github_token = config.github_token.get_secret_value() if config.github_token else None
            with GitHubRestClient(token=github_token, budget=budget, timeout=15.0, max_retries=0) as github:
                with OpenAICompatibleClient.from_config(config, budget=budget, timeout=45.0) as model:
                    result = run_research_task(req.task, model=model, github=github, budget=budget, events=events)
            report = render_mvp_report(req.task, result)
            status = _store.get(task_id)
            if status:
                status.status = result.status
                status.termination_reason = result.termination_reason
                status.report = report
                status.budget = result.budget
        except Exception as exc:
            status = _store.get(task_id)
            if status:
                status.status = "error"
                status.termination_reason = str(exc)
        finally:
            loop.call_soon_threadsafe(event_queue.put_nowait, None)

    asyncio.create_task(asyncio.to_thread(run_agent_sync))

    disconnected = False
    while True:
        try:
            item = await asyncio.wait_for(event_queue.get(), timeout=30.0)
            if item is None:
                break
            yield _sse("event", {
                "type": item.event_type,
                "details": _serializable(item.details),
            })
        except asyncio.TimeoutError:
            yield _sse("heartbeat", {})
        except asyncio.CancelledError:
            disconnected = True
            break

    if not disconnected:
        status = _store.get(task_id)
        yield _sse("done", {
            "task_id": task_id,
            "status": status.status if status else "unknown",
            "termination_reason": status.termination_reason if status else "",
            "report": status.report if status else "",
            "budget": status.budget if status else {},
        })


@web_app.get("/api/history")
async def history() -> list[dict]:
    return [
        {"id": s.id, "task": s.task, "status": s.status, "termination_reason": s.termination_reason}
        for s in _store.values()
    ]


@web_app.get("/api/report/{task_id}")
async def report(task_id: str) -> dict:
    status = _store.get(task_id)
    if not status:
        return {"error": "not_found"}
    return {
        "id": status.id,
        "task": status.task,
        "status": status.status,
        "termination_reason": status.termination_reason,
        "report": status.report,
        "budget": status.budget,
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _serializable(obj):
    if isinstance(obj, dict):
        return {k: _serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serializable(v) for v in obj]
    if isinstance(obj, SecretStr):
        return "<redacted>"
    if hasattr(obj, "model_dump"):
        return _serializable(obj.model_dump())
    if hasattr(obj, "__dict__"):
        return str(obj)
    return obj


def start_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    import uvicorn
    uvicorn.run(web_app, host=host, port=port, log_level="info")
