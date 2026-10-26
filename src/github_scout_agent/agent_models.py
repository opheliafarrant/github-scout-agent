"""Small protocol objects for the S3-03 model boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: str


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None

    @model_validator(mode="after")
    def require_content_or_tool_call(self) -> "AssistantResponse":
        if not self.tool_calls and not (self.content and self.content.strip()):
            raise ValueError("assistant response must contain content or tool_calls")
        return self

    def as_message(self) -> AgentMessage:
        return AgentMessage(
            role="assistant",
            content=self.content or "",
            tool_calls=self.tool_calls,
        )


class ModelCallError(RuntimeError):
    """Safe model boundary error used by real and mock adapters."""


class ModelProtocolError(RuntimeError):
    """Model response could not satisfy the local protocol."""
