"""Small in-memory, redacted execution event recorder."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


_SECRET_KEY = re.compile(r"token|api.?key|authorization|secret|password", re.IGNORECASE)
_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    event_type: str
    timestamp: float
    details: dict[str, Any] = Field(default_factory=dict)


def _redact(value: Any, key: str | None = None) -> Any:
    if key and _SECRET_KEY.search(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _BEARER.sub("Bearer <redacted>", value)[:400]
    return value


class EventRecorder:
    def __init__(self, *, clock: callable = time.monotonic) -> None:
        self.clock = clock
        self.events: list[AgentEvent] = []

    def emit(self, event_type: str, **details: Any) -> AgentEvent:
        event = AgentEvent(
            sequence=len(self.events) + 1,
            event_type=event_type,
            timestamp=self.clock(),
            details=_redact(details),
        )
        self.events.append(event)
        return event
