"""Validated data objects returned by the S3-02 read-only tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    captured_at: datetime
    field_path: str | None = None

    @field_validator("url")
    @classmethod
    def validate_https_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("source URL must use HTTPS")
        return value


class LicenseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["identified", "unknown"] = "unknown"
    key: str | None = None
    name: str | None = None
    spdx_id: str | None = None
    source: SourceRef | None = None


class ReadmeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "missing", "truncated", "unknown"] = "unknown"
    content: str | None = None
    content_size: int | None = Field(default=None, ge=0)
    truncated: bool = False
    sha: str | None = None
    trust_label: Literal["untrusted_remote_text"] = "untrusted_remote_text"
    source: SourceRef | None = None


class RepositoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: int | str | None = None
    full_name: str
    html_url: str
    description: str | None = None
    is_public: bool | None = None
    is_fork: bool | None = None
    is_archived: bool | None = None
    is_disabled: bool | None = None
    stargazers_count: int | None = Field(default=None, ge=0)
    forks_count: int | None = Field(default=None, ge=0)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    pushed_at: datetime | None = None
    language: str | None = None
    license: LicenseEvidence = Field(default_factory=LicenseEvidence)
    languages: dict[str, int] | None = None
    topics: list[str] | None = None
    readme: ReadmeEvidence = Field(default_factory=ReadmeEvidence)
    missing_fields: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RepositoryRecord]
    total_count: int | None = Field(default=None, ge=0)
    incomplete_results: bool = False
    query: str
    sort: str
    order: str
    page: int = Field(ge=1)
    per_page: int = Field(ge=1)
    candidate_budget_truncated: bool = False
    excluded_non_public_count: int = Field(default=0, ge=0)
    excluded_language_count: int = Field(default=0, ge=0)
    unverified_items: list[RepositoryRecord] = Field(default_factory=list)
    required_language: str | None = None
    required_languages: list[str] = Field(default_factory=list)
    source: SourceRef


class LanguagesTopics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    languages: dict[str, int]
    topics: list[str]
    source_refs: list[SourceRef]


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    category: str
    message: str
    retryable: bool = False
    status_code: int | None = None
    attempts: int = 1


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    ok: bool
    data: Any | None = None
    error: ToolError | None = None
    request_count: int = Field(default=0, ge=0)

    @field_validator("error")
    @classmethod
    def error_requires_failed_result(cls, value: ToolError | None, info: Any) -> ToolError | None:
        if value is not None and info.data.get("ok") is True:
            raise ValueError("successful tool results cannot contain an error")
        return value
