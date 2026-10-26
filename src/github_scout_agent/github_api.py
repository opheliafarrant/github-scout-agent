"""Bounded, GET-only GitHub REST access for S3-02.

This module never exposes a generic URL or HTTP method. Tests inject an
HTTPX transport; production use remains explicitly disabled until a later
stage adds an authorized integration path.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from .budget import BudgetExceeded, RequestBudget
from .github_models import (
    LanguagesTopics,
    LicenseEvidence,
    ReadmeEvidence,
    RepositoryRecord,
    SearchResult,
    SourceRef,
)


GITHUB_API_BASE = "https://api.github.com"
GITHUB_SOURCE_HOST = "github.com"
PART_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
DEFAULT_README_CHARS = 12_000
DEFAULT_MAX_RETRIES = 1


class GithubApiError(RuntimeError):
    """Safe, structured error from the fixed GitHub read-only boundary."""

    def __init__(
        self,
        code: str,
        category: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        attempts: int = 1,
    ) -> None:
        self.code = code
        self.category = category
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.attempts = attempts
        super().__init__(message)


def validate_owner_repo_part(value: str, field_name: str) -> str:
    if not value or not PART_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"invalid {field_name}")
    return value


def parse_public_repository_url(value: str) -> tuple[str, str]:
    """Parse only a github.com/owner/repo URL; reject arbitrary hosts/paths."""

    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc.lower() not in {
        GITHUB_SOURCE_HOST,
        f"www.{GITHUB_SOURCE_HOST}",
    }:
        raise ValueError("repository URL must be an HTTPS github.com URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise ValueError("repository URL must contain exactly owner and repo")
    repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    return (
        validate_owner_repo_part(parts[0], "owner"),
        validate_owner_repo_part(repo, "repo"),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source(url: str, captured_at: datetime, field_path: str | None = None) -> SourceRef:
    return SourceRef(url=url, captured_at=captured_at, field_path=field_path)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _normalize_repository(payload: Mapping[str, object], source_url: str, captured_at: datetime) -> RepositoryRecord:
    missing: list[str] = []

    def value_or_missing(name: str) -> object:
        value = payload.get(name)
        if value is None:
            missing.append(name)
        return value

    full_name = value_or_missing("full_name")
    html_url = value_or_missing("html_url")
    if not isinstance(full_name, str) or not isinstance(html_url, str):
        raise GithubApiError("invalid_repository_payload", "schema", "repository identity is missing")

    license_payload = payload.get("license")
    license_evidence = LicenseEvidence()
    if isinstance(license_payload, Mapping):
        key = license_payload.get("key")
        name = license_payload.get("name")
        spdx_id = license_payload.get("spdx_id")
        if any(isinstance(value, str) and value for value in (key, name, spdx_id)):
            license_evidence = LicenseEvidence(
                status="identified",
                key=key if isinstance(key, str) else None,
                name=name if isinstance(name, str) else None,
                spdx_id=spdx_id if isinstance(spdx_id, str) else None,
                source=_source(source_url, captured_at, "license"),
            )
        else:
            missing.append("license")
    else:
        missing.append("license")

    topics = payload.get("topics")
    normalized_topics = [topic for topic in topics if isinstance(topic, str)] if isinstance(topics, list) else None
    if normalized_topics is None:
        missing.append("topics")

    fields = {
        "repository_id": payload.get("id") if isinstance(payload.get("id"), (int, str)) else None,
        "full_name": full_name,
        "html_url": html_url,
        "description": payload.get("description") if isinstance(payload.get("description"), str) else None,
        "is_public": payload.get("private") is False if "private" in payload else None,
        "is_fork": payload.get("fork") if isinstance(payload.get("fork"), bool) else None,
        "is_archived": payload.get("archived") if isinstance(payload.get("archived"), bool) else None,
        "is_disabled": payload.get("disabled") if isinstance(payload.get("disabled"), bool) else None,
        "stargazers_count": _optional_int(payload.get("stargazers_count")),
        "forks_count": _optional_int(payload.get("forks_count")),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "pushed_at": payload.get("pushed_at"),
        "license": license_evidence,
        "topics": normalized_topics,
        "missing_fields": sorted(set(missing)),
        "source_refs": [_source(source_url, captured_at)],
    }
    for name in ("stargazers_count", "forks_count", "created_at", "updated_at", "pushed_at"):
        if fields[name] is None and name not in fields["missing_fields"]:
            fields["missing_fields"].append(name)
    return RepositoryRecord(**fields)


class GitHubRestClient:
    """Fixed-host, GET-only GitHub REST client with injectable transport."""

    def __init__(
        self,
        *,
        token: str | None = None,
        budget: RequestBudget | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 5.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_retry_delay: float = 5.0,
        readme_max_chars: int = DEFAULT_README_CHARS,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if readme_max_chars <= 0:
            raise ValueError("readme_max_chars must be positive")
        self.budget = budget or RequestBudget()
        self._token = token
        self._max_retries = max_retries
        self._sleep = sleep_fn
        self._max_retry_delay = max_retry_delay
        self._readme_max_chars = readme_max_chars
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-scout-agent/0.1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=GITHUB_API_BASE,
            headers=headers,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubRestClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _request_json(
        self,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        accept: str | None = None,
    ) -> tuple[object, str, datetime]:
        captured_at = _now()
        for attempt in range(1, self._max_retries + 2):
            try:
                self.budget.reserve_github_request()
                headers = {"Accept": accept} if accept else None
                response = self._client.get(path, params=params, headers=headers)
            except BudgetExceeded:
                raise
            except httpx.TimeoutException as exc:
                if attempt <= self._max_retries:
                    continue
                raise GithubApiError(
                    "timeout", "timeout", "GitHub request timed out", retryable=True, attempts=attempt
                ) from exc
            except httpx.RequestError as exc:
                if attempt <= self._max_retries:
                    continue
                raise GithubApiError(
                    "network_error", "network", "GitHub request failed", retryable=True, attempts=attempt
                ) from exc

            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > self.budget.max_response_bytes:
                raise GithubApiError(
                    "response_too_large", "budget", "GitHub response exceeds the response-size limit"
                )
            content = response.content
            try:
                self.budget.record_response_size(len(content))
            except BudgetExceeded as exc:
                raise GithubApiError("response_too_large", "budget", "GitHub response exceeds the response-size limit") from exc

            status = response.status_code
            if 200 <= status < 300:
                try:
                    return response.json(), str(response.request.url), captured_at
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise GithubApiError("invalid_json", "schema", "GitHub returned invalid JSON", attempts=attempt) from exc

            if status == 404:
                raise GithubApiError("not_found", "not_found", "GitHub resource was not found", status_code=status, attempts=attempt)
            if status == 401:
                raise GithubApiError("unauthorized", "auth", "GitHub authentication was rejected", status_code=status, attempts=attempt)
            if status == 422:
                raise GithubApiError("validation_failed", "input", "GitHub rejected the request parameters", status_code=status, attempts=attempt)

            rate_limited = status == 429 or (
                status == 403 and response.headers.get("x-ratelimit-remaining") == "0"
            )
            if rate_limited:
                delay = self._retry_delay(response.headers)
                if attempt <= self._max_retries and delay <= self._max_retry_delay:
                    self._sleep(delay)
                    continue
                raise GithubApiError(
                    "rate_limited", "rate_limit", "GitHub rate limit blocked the request", retryable=True,
                    status_code=status, attempts=attempt
                )
            if status == 403:
                raise GithubApiError(
                    "forbidden", "permission", "GitHub denied the read-only request", status_code=status, attempts=attempt
                )
            if 500 <= status < 600 and attempt <= self._max_retries:
                continue
            raise GithubApiError(
                "upstream_error", "upstream", "GitHub returned an upstream error", retryable=status >= 500,
                status_code=status, attempts=attempt
            )
        raise AssertionError("request loop must return or raise")

    def _retry_delay(self, headers: Mapping[str, str]) -> float:
        raw = headers.get("retry-after")
        if raw is None:
            return 0.0
        try:
            return max(float(raw), 0.0)
        except ValueError:
            return self._max_retry_delay + 1

    def search_public_repositories(
        self,
        *,
        query: str,
        sort: str = "best_match",
        order: str = "desc",
        page: int = 1,
        per_page: int = 10,
    ) -> SearchResult:
        if not query or len(query) > 256 or any(ord(char) < 32 for char in query):
            raise GithubApiError("invalid_query", "input", "query is empty, too long, or contains control characters")
        if page < 1 or per_page < 1 or per_page > 100:
            raise GithubApiError("invalid_pagination", "input", "pagination values are outside the allowed range")
        if order not in {"asc", "desc"}:
            raise GithubApiError("invalid_order", "input", "order must be asc or desc")
        params: dict[str, object] = {"q": query, "page": page, "per_page": per_page}
        if sort != "best_match":
            if sort not in {"stars", "forks", "help-wanted-issues", "updated"}:
                raise GithubApiError("invalid_sort", "input", "sort is not allowed")
            params["sort"] = sort
            params["order"] = order
        payload, source_url, captured_at = self._request_json("/search/repositories", params=params)
        if not isinstance(payload, Mapping) or not isinstance(payload.get("items"), list):
            raise GithubApiError("invalid_search_payload", "schema", "GitHub search response is missing items")
        raw_items = [item for item in payload["items"] if isinstance(item, Mapping)]
        accepted_count, truncated = self.budget.take_candidates(len(raw_items))
        items = [_normalize_repository(item, source_url, captured_at) for item in raw_items[:accepted_count]]
        return SearchResult(
            items=items,
            total_count=payload.get("total_count") if isinstance(payload.get("total_count"), int) else None,
            incomplete_results=bool(payload.get("incomplete_results", False)),
            query=query,
            sort=sort,
            order=order,
            page=page,
            per_page=per_page,
            candidate_budget_truncated=truncated,
            source=_source(source_url, captured_at),
        )

    def get_public_repository(self, *, owner: str, repo: str) -> RepositoryRecord:
        owner = validate_owner_repo_part(owner, "owner")
        repo = validate_owner_repo_part(repo, "repo")
        payload, source_url, captured_at = self._request_json(f"/repos/{owner}/{repo}")
        if not isinstance(payload, Mapping):
            raise GithubApiError("invalid_repository_payload", "schema", "GitHub repository response is not an object")
        if payload.get("private") is True or payload.get("visibility") not in {None, "public"}:
            raise GithubApiError("private_repository_blocked", "safety", "private repositories are not supported")
        return _normalize_repository(payload, source_url, captured_at)

    def get_public_readme(self, *, owner: str, repo: str, ref: str | None = None) -> ReadmeEvidence:
        owner = validate_owner_repo_part(owner, "owner")
        repo = validate_owner_repo_part(repo, "repo")
        params = {"ref": ref} if ref else None
        try:
            payload, source_url, captured_at = self._request_json(
                f"/repos/{owner}/{repo}/readme", params=params, accept="application/vnd.github.object+json"
            )
        except GithubApiError as exc:
            if exc.code == "not_found":
                return ReadmeEvidence(status="missing", source=None)
            raise
        if not isinstance(payload, Mapping):
            raise GithubApiError("invalid_readme_payload", "schema", "README response is not an object")
        raw_content = payload.get("content")
        if not isinstance(raw_content, str):
            return ReadmeEvidence(status="unknown", source=_source(source_url, captured_at))
        content = raw_content
        encoding = payload.get("encoding")
        if encoding == "base64":
            try:
                content = base64.b64decode(raw_content, validate=False).decode("utf-8", errors="replace")
            except (binascii.Error, ValueError) as exc:
                raise GithubApiError("invalid_readme_encoding", "schema", "README content encoding is invalid") from exc
        content_size = payload.get("size") if isinstance(payload.get("size"), int) else len(content.encode("utf-8"))
        truncated = len(content) > self._readme_max_chars
        return ReadmeEvidence(
            status="truncated" if truncated else "available",
            content=content[: self._readme_max_chars],
            content_size=content_size,
            truncated=truncated,
            sha=payload.get("sha") if isinstance(payload.get("sha"), str) else None,
            source=_source(source_url, captured_at),
        )

    def get_repository_license(self, *, owner: str, repo: str) -> LicenseEvidence:
        owner = validate_owner_repo_part(owner, "owner")
        repo = validate_owner_repo_part(repo, "repo")
        try:
            payload, source_url, captured_at = self._request_json(f"/repos/{owner}/{repo}/license")
        except GithubApiError as exc:
            if exc.code == "not_found":
                return LicenseEvidence(status="unknown")
            raise
        if not isinstance(payload, Mapping):
            raise GithubApiError("invalid_license_payload", "schema", "license response is not an object")
        license_payload = payload.get("license") if isinstance(payload.get("license"), Mapping) else payload
        key = license_payload.get("key") if isinstance(license_payload, Mapping) else None
        name = license_payload.get("name") if isinstance(license_payload, Mapping) else None
        spdx_id = license_payload.get("spdx_id") if isinstance(license_payload, Mapping) else None
        if not any(isinstance(value, str) and value for value in (key, name, spdx_id)):
            return LicenseEvidence(status="unknown")
        return LicenseEvidence(
            status="identified",
            key=key if isinstance(key, str) else None,
            name=name if isinstance(name, str) else None,
            spdx_id=spdx_id if isinstance(spdx_id, str) else None,
            source=_source(source_url, captured_at),
        )

    def get_repository_languages_and_topics(self, *, owner: str, repo: str) -> LanguagesTopics:
        owner = validate_owner_repo_part(owner, "owner")
        repo = validate_owner_repo_part(repo, "repo")
        languages_payload, languages_url, languages_captured_at = self._request_json(f"/repos/{owner}/{repo}/languages")
        topics_payload, topics_url, topics_captured_at = self._request_json(f"/repos/{owner}/{repo}/topics")
        if not isinstance(languages_payload, Mapping):
            raise GithubApiError("invalid_languages_payload", "schema", "languages response is not an object")
        if not isinstance(topics_payload, Mapping):
            raise GithubApiError("invalid_topics_payload", "schema", "topics response is not an object")
        languages = {
            str(name): value for name, value in languages_payload.items() if isinstance(name, str) and isinstance(value, int) and value >= 0
        }
        names = topics_payload.get("names", [])
        topics = [name for name in names if isinstance(name, str)] if isinstance(names, list) else []
        return LanguagesTopics(
            languages=languages,
            topics=topics,
            source_refs=[
                _source(languages_url, languages_captured_at),
                _source(topics_url, topics_captured_at),
            ],
        )
