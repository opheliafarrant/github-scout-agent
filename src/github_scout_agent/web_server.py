"""FastAPI web server with SSE streaming for the GitHub Scout Agent."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from .application import run_research_task, render_mvp_report
from .budget import RequestBudget
from .config import (
    AppConfig,
    ENV_MODEL,
    ENV_MODEL_API_KEY,
    ENV_MODEL_BASE_URL,
    default_history_path,
    default_settings_path,
    load_config,
    mask_secret,
    model_configuration_status,
    read_settings,
    write_settings,
)
from .events import AgentEvent, EventRecorder
from .github_api import GitHubRestClient
from .model_client import ModelAdapterError, OpenAICompatibleClient, list_model_ids


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

web_app = FastAPI(title="GitHub Scout Agent", version="1.1.0")


class TaskRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=2000)
    max_candidates: int = Field(default=3, ge=1, le=10)
    max_model_calls: int = Field(default=12, ge=1, le=30)
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


class HistoryStore:
    """Thread-safe, file-backed storage for Web UI research tasks."""

    def __init__(
        self,
        file_path: Path | None = None,
        max_entries: int = 100,
    ) -> None:
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskStatus] = {}
        self._file_path = file_path or self._resolve_path()
        self._load()

    @staticmethod
    def _resolve_path() -> Path:
        return default_history_path()

    @property
    def file_path(self) -> Path:
        return self._file_path

    def _load(self) -> None:
        with self._lock:
            self._tasks.clear()
            if not self._file_path.is_file():
                return
            try:
                with open(self._file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            status = TaskStatus(**data)
                            self._tasks[status.id] = status
                        except Exception:
                            continue
                if len(self._tasks) > self.max_entries:
                    excess = len(self._tasks) - self.max_entries
                    for k in list(self._tasks.keys())[:excess]:
                        del self._tasks[k]
            except Exception:
                pass

    def _persist_locked(self) -> None:
        """Write in-memory tasks atomically to disk while holding lock."""
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._file_path.with_suffix(f".tmp_{uuid.uuid4().hex[:8]}")
            with open(tmp_path, "w", encoding="utf-8") as f:
                for status in self._tasks.values():
                    f.write(status.model_dump_json() + "\n")
            os.replace(tmp_path, self._file_path)
        except Exception:
            pass

    def get(self, task_id: str) -> TaskStatus | None:
        with self._lock:
            return self._tasks.get(task_id)

    def set(self, status: TaskStatus) -> None:
        with self._lock:
            self._tasks[status.id] = status
            if len(self._tasks) > self.max_entries:
                excess = len(self._tasks) - self.max_entries
                for k in list(self._tasks.keys())[:excess]:
                    del self._tasks[k]
            self._persist_locked()

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        termination_reason: str | None = None,
        report: str | None = None,
        budget: dict | None = None,
    ) -> TaskStatus | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if status is not None:
                task.status = status
            if termination_reason is not None:
                task.termination_reason = termination_reason
            if report is not None:
                task.report = report
            if budget is not None:
                task.budget = budget
            self._persist_locked()
            return task

    def values(self) -> list[TaskStatus]:
        with self._lock:
            return list(self._tasks.values())

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()
            if self._file_path.is_file():
                try:
                    self._file_path.unlink()
                except Exception:
                    pass

    def __getitem__(self, task_id: str) -> TaskStatus:
        with self._lock:
            return self._tasks[task_id]

    def __setitem__(self, task_id: str, status: TaskStatus) -> None:
        self.set(status)

    def __contains__(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._tasks

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)


_store = HistoryStore()


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


class SettingsPayload(BaseModel):
    """Write-only settings payload; secrets are never echoed back."""

    model_config = ConfigDict(extra="forbid")

    model_base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    model_api_key: str | None = Field(default=None, max_length=4096)
    github_token: str | None = Field(default=None, max_length=4096)
    thinking_mode: Literal["disabled", "enabled"] | None = None
    strict_tools: bool | None = None
    max_tokens: int | None = Field(default=None, gt=0)


def _settings_view() -> dict:
    config = load_config()
    stored = read_settings()
    settings_path = default_settings_path()
    environment_configured = any(
        os.environ.get(name) for name in (ENV_MODEL_BASE_URL, ENV_MODEL, ENV_MODEL_API_KEY)
    )
    if environment_configured:
        source = "environment"
    elif stored:
        source = "settings_file"
    else:
        source = "none"
    api_key = config.model_api_key.get_secret_value() if config.model_api_key else None
    github_token = config.github_token.get_secret_value() if config.github_token else None
    return {
        "ready": config.model_ready,
        "status": model_configuration_status(config),
        "model": config.model or "",
        "base_url": config.model_base_url or "",
        "has_key": api_key is not None,
        "key_hint": mask_secret(api_key),
        "has_github_token": github_token is not None,
        "github_token_hint": mask_secret(github_token),
        "thinking_mode": config.thinking_mode or "disabled",
        "strict_tools": config.strict_tools,
        "max_tokens": config.max_tokens,
        "settings_path": str(settings_path),
        "settings_exists": settings_path.is_file(),
        "source": source,
    }


def _validation_detail(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "invalid value"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "Invalid settings"


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



@web_app.get("/api/settings")
async def settings_status() -> dict:
    return _settings_view()


@web_app.put("/api/settings")
async def update_settings(payload: SettingsPayload) -> dict:
    updates: dict[str, object] = {}
    if payload.model_base_url is not None:
        updates["model_base_url"] = payload.model_base_url.strip() or None
    if payload.model is not None:
        updates["model"] = payload.model.strip() or None
    if payload.model_api_key is not None and payload.model_api_key.strip():
        updates["model_api_key"] = payload.model_api_key.strip()
    if payload.github_token is not None and payload.github_token.strip():
        updates["github_token"] = payload.github_token.strip()
    if payload.thinking_mode is not None:
        updates["thinking_mode"] = payload.thinking_mode
    if payload.strict_tools is not None:
        updates["strict_tools"] = payload.strict_tools
    if payload.max_tokens is not None:
        updates["max_tokens"] = payload.max_tokens

    merged = {
        key: value
        for key, value in {**read_settings(), **updates}.items()
        if value not in (None, "")
    }

    try:
        AppConfig(
            model_base_url=merged.get("model_base_url"),
            model=merged.get("model"),
            model_api_key=SecretStr(str(merged["model_api_key"])) if merged.get("model_api_key") else None,
            github_token=SecretStr(str(merged["github_token"])) if merged.get("github_token") else None,
            thinking_mode=merged.get("thinking_mode"),
            strict_tools=bool(merged.get("strict_tools", True)),
            max_tokens=merged.get("max_tokens"),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc

    write_settings(merged)
    return _settings_view()


class ModelListPayload(BaseModel):
    """Probe payload: an explicit endpoint/key, or the values already saved."""

    model_config = ConfigDict(extra="forbid")

    model_base_url: str | None = Field(default=None, max_length=500)
    model_api_key: str | None = Field(default=None, max_length=4096)


_MODEL_LIST_REASONS = {
    "model_list_auth": "auth",
    "model_list_unsupported": "unsupported",
    "model_list_timeout": "timeout",
    "model_list_network": "network",
    "model_list_payload": "payload",
    "model_list_empty": "empty",
}


@web_app.post("/api/settings/models")
async def list_available_models(payload: ModelListPayload) -> dict:
    stored = read_settings()
    base_url = (payload.model_base_url or "").strip() or str(stored.get("model_base_url") or "")
    api_key = (payload.model_api_key or "").strip()
    used_saved_key = False
    if not api_key:
        saved_key = stored.get("model_api_key")
        if saved_key:
            api_key = str(saved_key)
            used_saved_key = True
    if not base_url:
        return {"ok": False, "reason": "invalid", "message": "请先填写 API 地址。", "used_saved_key": used_saved_key}
    try:
        AppConfig(model_base_url=base_url)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
    try:
        models = await asyncio.to_thread(list_model_ids, base_url, api_key or None)
    except ModelAdapterError as exc:
        return {
            "ok": False,
            "reason": _MODEL_LIST_REASONS.get(exc.code, "unknown"),
            "message": str(exc),
            "status_code": exc.status_code,
            "used_saved_key": used_saved_key,
        }
    return {"ok": True, "models": models, "count": len(models), "used_saved_key": used_saved_key}


@web_app.post("/api/run")
async def run_task(req: TaskRequest, request: Request) -> StreamingResponse:
    task_id = f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    status = TaskStatus(id=task_id, task=req.task, status="running", started_at=time.time())
    _store.set(status)

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
        yield _sse("error", {
            "code": "not_configured",
            "message": "Model configuration incomplete. Open Settings (gear icon) and fill in the endpoint, model name and API key.",
        })
        _store.update(task_id, status="error", termination_reason="configuration")
        return

    budget = RequestBudget(
        max_model_calls=req.max_model_calls,
        max_github_requests=req.max_github_requests,
        max_candidates=req.max_candidates,
        max_task_seconds=req.max_task_seconds,
        max_feedback_bytes=128 * 1024,
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
                with OpenAICompatibleClient.from_config(config, budget=budget, timeout=45.0, max_retries=2) as model:
                    result = run_research_task(req.task, model=model, github=github, budget=budget, events=events)
            report = render_mvp_report(req.task, result)
            _store.update(
                task_id,
                status=result.status,
                termination_reason=result.termination_reason,
                report=report,
                budget=result.budget,
            )
        except Exception as exc:
            _store.update(task_id, status="error", termination_reason=str(exc))
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
