"""Bounded, GET-only GitHub REST access for S3-02.

This module never exposes a generic URL or HTTP method. Tests inject an
HTTPX transport; production use remains explicitly disabled until a later
stage adds an authorized integration path.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

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
DEFAULT_FALLBACK_RETRY_DELAY = 1.0
MAX_SEARCH_PER_PAGE = 5

# Curated canonical slugs for well-known projects that GitHub search may not
# index reliably (for example after a rename).  Keys are normalized with
# _normalize_known_project_key; values are the canonical "owner/repo" form.
KNOWN_PROJECTS: dict[str, str] = {
    "cline": "cline/cline",
    "aider": "Aider-AI/aider",
    "openhands": "OpenHands/OpenHands",
    "sweagent": "SWE-agent/SWE-agent",
    "minisweagent": "SWE-agent/mini-swe-agent",
}


def _normalize_known_project_key(value: str) -> str:
    """Collapse a project name to a search-index-independent lookup key."""
    return re.sub(r"[^a-z0-9]", "", value.casefold())

# Fields extracted from search result items that _normalize_repository and
# _is_public_repository actually read.  Everything else in the raw JSON
# (60+ fields per item) is discarded before response-bytes are recorded.
_SEARCH_ITEM_FIELDS: set[str] = {
    "full_name", "html_url", "description",
    "private", "visibility", "fork", "archived", "disabled",
    "stargazers_count", "forks_count", "open_issues_count", "watchers_count",
    "created_at", "updated_at", "pushed_at",
    "language", "id",
    "license", "topics",
    "has_issues", "has_projects", "has_downloads",
    "has_wiki", "has_pages", "has_discussions",
    "default_branch", "owner",
}


def _trim_search_payload(raw: object) -> dict[str, object]:
    """Keep only the fields from *raw* that the agent actually consumes."""
    if not isinstance(raw, Mapping):
        return {} if raw is None else {"_raw": str(raw)[:256]}
    trimmed: dict[str, object] = {}
    for key, value in raw.items():
        if key in _SEARCH_ITEM_FIELDS:
            if key == "license" and isinstance(value, Mapping):
                trimmed[key] = {"key": value.get("key"), "name": value.get("name"), "spdx_id": value.get("spdx_id")}
            elif key == "owner" and isinstance(value, Mapping):
                trimmed[key] = {"login": value.get("login")}
            elif key == "topics" and isinstance(value, list):
                trimmed[key] = [t for t in value if isinstance(t, str)]
            else:
                trimmed[key] = value
    return trimmed


def _serialized_size(data: object) -> int:
    """Return the UTF-8 byte length of the JSON-serialized *data*."""
    return len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


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


_LANGUAGE_ALIASES = {
    "py": "Python",
    "python": "Python",
    "c++": "C++",
    "cpp": "C++",
    "cxx": "C++",
    "javascript": "JavaScript",
    "js": "JavaScript",
    "typescript": "TypeScript",
    "ts": "TypeScript",
    "go": "Go",
    "golang": "Go",
    "rust": "Rust",
    "java": "Java",
    "c#": "C#",
    "csharp": "C#",
    "ruby": "Ruby",
    "php": "PHP",
    "swift": "Swift",
    "kotlin": "Kotlin",
}


def normalize_language_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"\s+", " ", value.strip().casefold())
    return _LANGUAGE_ALIASES.get(normalized, value.strip())


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

    language_value = payload.get("language")
    language = language_value.strip() if isinstance(language_value, str) and language_value.strip() else None
    if language is None:
        missing.append("language")

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
        "language": language,
        "license": license_evidence,
        "topics": normalized_topics,
        "missing_fields": sorted(set(missing)),
        "source_refs": [_source(source_url, captured_at)],
    }
    for name in ("stargazers_count", "forks_count", "created_at", "updated_at", "pushed_at"):
        if fields[name] is None and name not in fields["missing_fields"]:
            fields["missing_fields"].append(name)
    try:
        return RepositoryRecord(**fields)
    except ValidationError as exc:
        raise GithubApiError(
            "invalid_github_response",
            "schema",
            "GitHub response data failed local validation",
        ) from exc


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
        now_fn: Callable[[], float] = time.time,
        max_retry_delay: float = 5.0,
        fallback_retry_delay: float = DEFAULT_FALLBACK_RETRY_DELAY,
        readme_max_chars: int = DEFAULT_README_CHARS,
        required_language: str | None = None,
        required_languages: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if max_retry_delay < 0 or fallback_retry_delay <= 0:
            raise ValueError("retry delays must be non-negative, with a positive fallback delay")
        if readme_max_chars <= 0:
            raise ValueError("readme_max_chars must be positive")
        self.budget = budget or RequestBudget()
        self._token = token
        self._max_retries = max_retries
        self._sleep = sleep_fn
        self._now = now_fn
        self._max_retry_delay = max_retry_delay
        self._fallback_retry_delay = fallback_retry_delay
        self._readme_max_chars = readme_max_chars
        requested_languages = list(required_languages or ())
        if required_language:
            requested_languages.append(required_language)
        normalized_languages: list[str] = []
        seen_language_keys: set[str] = set()
        for value in requested_languages:
            language = normalize_language_name(value)
            if language and language.casefold() not in seen_language_keys:
                seen_language_keys.add(language.casefold())
                normalized_languages.append(language)
        self.required_languages = tuple(normalized_languages)
        self.required_language = self.required_languages[0] if len(self.required_languages) == 1 else None
        self.request_log: list[dict[str, object]] = []
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-scout-agent/1.0.0",
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

    @property
    def _required_language_keys(self) -> frozenset[str]:
        """Derive matching keys from the task's current language constraints."""

        return frozenset(language.casefold() for language in self.required_languages)

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
        skip_budget_recording: bool = False,
    ) -> tuple[object, str, datetime]:
        captured_at = _now()
        for attempt in range(1, self._max_retries + 2):
            try:
                self.budget.reserve_github_request()
                headers = {"Accept": accept} if accept else None
                response_context = self._client.stream("GET", path, params=params, headers=headers)
                with response_context as response:
                    if not skip_budget_recording:
                        content_length = response.headers.get("content-length")
                        if content_length and content_length.isdigit() and int(content_length) > self.budget.max_response_bytes:
                            raise GithubApiError(
                                "response_too_large", "budget", "GitHub response exceeds the response-size limit"
                            )
                    chunks: list[bytes] = []
                    response_size = 0
                    if skip_budget_recording:
                        # Download first, record trimmed size later (search responses
                        # are trimmed to ~15 fields per item before budget accounting).
                        try:
                            for chunk in response.iter_bytes():
                                chunks.append(chunk)
                        except httpx.RemoteProtocolError:
                            pass
                    else:
                        try:
                            for chunk in response.iter_bytes():
                                response_size += len(chunk)
                                self.budget.record_response_chunk(len(chunk), response_size)
                                chunks.append(chunk)
                        except BudgetExceeded as exc:
                            raise GithubApiError(
                                "response_too_large", "budget", "GitHub response exceeds the response-size limit"
                            ) from exc
                    content = b"".join(chunks)
                    status = response.status_code
                    response_url = str(response.request.url)
                    response_headers = response.headers
                    self.request_log.append(
                        {
                            "method": "GET",
                            "host": response.request.url.host,
                            "path": response.request.url.path,
                            "status_code": status,
                            "attempt": attempt,
                            "response_bytes": response_size,
                        }
                    )
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

            if 200 <= status < 300:
                try:
                    return json.loads(content), response_url, captured_at
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise GithubApiError("invalid_json", "schema", "GitHub returned invalid JSON", attempts=attempt) from exc

            if status == 404:
                raise GithubApiError("not_found", "not_found", "GitHub resource was not found", status_code=status, attempts=attempt)
            if status == 401:
                raise GithubApiError("unauthorized", "auth", "GitHub authentication was rejected", status_code=status, attempts=attempt)
            if status == 422:
                raise GithubApiError("validation_failed", "input", "GitHub rejected the request parameters", status_code=status, attempts=attempt)

            rate_limited = status == 429 or (
                status == 403
                and (
                    response_headers.get("x-ratelimit-remaining") == "0"
                    or "retry-after" in response_headers
                )
            )
            if rate_limited:
                delay = self._rate_limit_delay(response_headers)
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

    def _rate_limit_delay(self, headers: Mapping[str, str]) -> float:
        delays: list[float] = []
        retry_after = headers.get("retry-after")
        if retry_after is not None:
            try:
                parsed_retry_after = float(retry_after)
                if math.isfinite(parsed_retry_after) and parsed_retry_after >= 0:
                    delays.append(parsed_retry_after)
            except ValueError:
                pass

        reset = headers.get("x-ratelimit-reset")
        if reset is not None:
            try:
                reset_epoch = float(reset)
                current_time = self._now()
                if math.isfinite(reset_epoch) and reset_epoch > current_time:
                    delays.append(reset_epoch - current_time)
            except ValueError:
                pass

        if delays:
            return max(delays)
        return max(self._fallback_retry_delay, 0.0)

    def search_public_repositories(
        self,
        *,
        query: str,
        sort: str = "best_match",
        order: str = "desc",
        page: int = 1,
        per_page: int = MAX_SEARCH_PER_PAGE,
    ) -> SearchResult:
        if not query or len(query) > 256 or any(ord(char) < 32 for char in query):
            raise GithubApiError("invalid_query", "input", "query is empty, too long, or contains control characters")
        if page < 1 or per_page < 1 or per_page > MAX_SEARCH_PER_PAGE:
            raise GithubApiError("invalid_pagination", "input", "pagination values are outside the allowed range")
        if order not in {"asc", "desc"}:
            raise GithubApiError("invalid_order", "input", "order must be asc or desc")
        params: dict[str, object] = {"q": query, "page": page, "per_page": per_page}
        if sort != "best_match":
            if sort not in {"stars", "forks", "help-wanted-issues", "updated"}:
                raise GithubApiError("invalid_sort", "input", "sort is not allowed")
            params["sort"] = sort
            params["order"] = order
        payload, source_url, captured_at = self._request_json("/search/repositories", params=params, skip_budget_recording=True)
        if not isinstance(payload, Mapping) or not isinstance(payload.get("items"), list):
            raise GithubApiError("invalid_search_payload", "schema", "GitHub search response is missing items")
        raw_items = [item for item in payload["items"] if isinstance(item, Mapping)]
        # Trim every item to only the 20 fields the agent consumes; discard
        # 40+ unused fields (avatar URLs, API links, node IDs, etc.).
        trimmed_items = [_trim_search_payload(item) for item in raw_items]
        # Build a minimal trimmed payload for budget accounting.
        trimmed_payload = {
            "total_count": payload.get("total_count"),
            "incomplete_results": payload.get("incomplete_results", False),
            "items": trimmed_items,
        }
        try:
            self.budget.record_response_size(_serialized_size(trimmed_payload))
        except BudgetExceeded as exc:
            raise GithubApiError(
                "response_too_large", "budget", "GitHub search response exceeds the response-size limit"
            ) from exc
        public_items: list[Mapping[str, object]] = []
        unverified_items: list[Mapping[str, object]] = []
        excluded_language_count = 0
        public_item_count = 0
        seen_ids: set[str] = set()
        for item in trimmed_items:
            identity = self._candidate_identity(item)
            if self._is_public_repository(item) and identity and identity not in seen_ids:
                seen_ids.add(identity)
                public_item_count += 1
                if self.required_languages:
                    raw_language = item.get("language")
                    language = normalize_language_name(raw_language) if isinstance(raw_language, str) else None
                    if language is None:
                        unverified_items.append(item)
                    elif language.casefold() in self._required_language_keys:
                        public_items.append(item)
                    else:
                        excluded_language_count += 1
                else:
                    public_items.append(item)
        candidate_ids = [self._candidate_identity(item) for item in public_items]
        max_candidates = self.budget.max_candidates
        truncated = len(public_items) > max_candidates
        accepted_id_set = set(
            cid for cid in candidate_ids if cid is not None
        )
        items = [
            _normalize_repository(item, source_url, captured_at)
            for item in public_items
        ]
        unverified_records = [_normalize_repository(item, source_url, captured_at) for item in unverified_items]
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
            excluded_non_public_count=len(raw_items) - public_item_count,
            excluded_language_count=excluded_language_count,
            unverified_items=unverified_records,
            required_language=self.required_language,
            required_languages=list(self.required_languages),
            source=_source(source_url, captured_at),
        )

    @staticmethod
    def _is_public_repository(payload: Mapping[str, object]) -> bool:
        return payload.get("private") is False and payload.get("visibility") == "public"

    @staticmethod
    def _candidate_identity(payload: Mapping[str, object]) -> str | None:
        full_name = payload.get("full_name")
        return full_name.casefold() if isinstance(full_name, str) and full_name else None

    def get_public_repository(self, *, owner: str, repo: str) -> RepositoryRecord:
        owner = validate_owner_repo_part(owner, "owner")
        repo = validate_owner_repo_part(repo, "repo")
        requested_identity = f"{owner}/{repo}"
        payload, source_url, captured_at = self._request_json(f"/repos/{owner}/{repo}")
        if not isinstance(payload, Mapping):
            raise GithubApiError("invalid_repository_payload", "schema", "GitHub repository response is not an object")
        if payload.get("private") is not False or payload.get("visibility") != "public":
            raise GithubApiError("private_repository_blocked", "safety", "private repositories are not supported")
        record = _normalize_repository(payload, source_url, captured_at)
        if (
            self.required_languages
            and record.language
            and record.language.casefold() not in self._required_language_keys
        ):
            raise GithubApiError(
                "language_mismatch",
                "safety",
                "repository language does not satisfy the requested language constraint",
            )
        accepted, _ = self.budget.take_candidate_ids([record.full_name])
        if not accepted:
            raise GithubApiError(
                "candidate_capacity_full",
                "safety",
                "candidate capacity is full; repository metadata for a new candidate is blocked",
            )
        return record

    def get_known_repository(self, *, name: str) -> RepositoryRecord:
        if not name or len(name) > 128 or any(ord(char) < 32 for char in name):
            raise GithubApiError("invalid_name", "input", "name is empty, too long, or contains control characters")
        key = _normalize_known_project_key(name)
        slug = KNOWN_PROJECTS.get(key)
        if slug is None:
            available = ", ".join(sorted(KNOWN_PROJECTS.values()))
            raise GithubApiError(
                "unknown_known_project", "input",
                f"'{name}' does not match a curated known project; known projects: {available}",
            )
        owner, repo = slug.split("/", 1)
        return self.get_public_repository(owner=owner, repo=repo)

    def get_public_readme(self, *, owner: str, repo: str, ref: str | None = None) -> ReadmeEvidence:
        owner = validate_owner_repo_part(owner, "owner")
        repo = validate_owner_repo_part(repo, "repo")
        requested_identity = f"{owner}/{repo}"
        accepted, _ = self.budget.take_candidate_ids([requested_identity])
        if not accepted and self.budget.candidates_full:
            raise GithubApiError(
                "candidate_capacity_full",
                "safety",
                "candidate capacity is full; README for a new candidate is blocked",
            )
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

    def replace_candidate_with_readme(
        self, *, source_owner: str, source_repo: str, target_owner: str, target_repo: str
    ) -> ReadmeEvidence:
        """Drop *source* and accept *target* as replacement with its README.

        Use when README evidence reveals that an accepted candidate has been
        superseded, deprecated, or was a mistaken selection.  The *source*
        identity must already be in the accepted candidate set.
        """
        source_id = (
            f"{validate_owner_repo_part(source_owner, 'source_owner')}"
            f"/{validate_owner_repo_part(source_repo, 'source_repo')}"
        )
        if not self.budget.replace_candidate_ids(source_id):
            raise GithubApiError(
                "candidate_not_found",
                "input",
                f"'{source_id}' is not an accepted candidate; cannot replace",
            )
        return self.get_public_readme(owner=target_owner, repo=target_repo)

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
