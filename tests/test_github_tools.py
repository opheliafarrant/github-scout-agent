from __future__ import annotations

import base64
import json

import httpx
import pytest

from github_scout_agent.budget import RequestBudget
from github_scout_agent.github_api import GitHubRestClient, parse_public_repository_url
from github_scout_agent.tool_registry import build_github_tool_registry


def _transport(handler, seen: list[httpx.Request]) -> httpx.MockTransport:
    def dispatch(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(dispatch)


def _response(request: httpx.Request, status: int, payload: object, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers, request=request)


def _repo(full_name: str = "octo/demo", *, archived: bool = False) -> dict[str, object]:
    return {
        "id": 123,
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "description": "test repository",
        "private": False,
        "visibility": "public",
        "fork": False,
        "archived": archived,
        "disabled": False,
        "stargazers_count": 12,
        "forks_count": 3,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-02-01T00:00:00Z",
        "pushed_at": "2024-03-01T00:00:00Z",
        "license": None,
        "topics": ["testing"],
    }


def test_search_normalizes_fields_and_preserves_missing_license() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        return _response(
            request,
            200,
            {"total_count": 1, "incomplete_results": False, "items": [_repo(archived=True)]},
        )

    budget = RequestBudget(max_candidates=3)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", json.dumps({"query": "testing"})
        )

    assert result.ok is True
    assert result.request_count == 1
    record = result.data.items[0]
    assert record.full_name == "octo/demo"
    assert record.stargazers_count == 12
    assert record.forks_count == 3
    assert record.is_archived is True
    assert record.license.status == "unknown"
    assert record.license.key is None
    assert "license" in record.missing_fields
    assert len(seen) == 1


def test_repository_language_metadata_is_preserved_or_unknown() -> None:
    cases = [("Python", "Python"), ("C++", "C++"), (None, None), ("__missing__", None)]
    for language, expected in cases:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request, language: str | None = language) -> httpx.Response:
            payload = _repo()
            if language == "__missing__":
                payload.pop("language", None)
            else:
                payload["language"] = language
            return _response(request, 200, {"total_count": 1, "items": [payload]})

        with GitHubRestClient(transport=_transport(handler, seen)) as client:
            result = build_github_tool_registry(client).dispatch(
                "search_public_repositories", {"query": "language"}
            )

        record = result.data.items[0]
        assert record.language == expected
        assert ("language" in record.missing_fields) is (expected is None)


def test_invalid_github_repository_field_becomes_controlled_schema_error() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _repo() | {"language": "Python", "created_at": "not-a-date"}
        return _response(request, 200, {"total_count": 1, "items": [payload]})

    budget = RequestBudget(max_candidates=3)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "invalid-date"}
        )

    assert result.ok is False
    assert result.error.code == "invalid_github_response"
    assert result.error.category == "schema"
    assert result.request_count == 1
    assert budget.github_requests_used == 1
    assert "not-a-date" not in result.error.message
    assert len(seen) == 1


def test_required_language_filters_known_mismatch_without_consuming_capacity() -> None:
    seen: list[httpx.Request] = []
    items = [
        _repo("octo/python") | {"language": "Python"},
        _repo("octo/cpp") | {"language": "C++"},
        _repo("octo/unknown"),
        _repo("octo/python-two") | {"language": "Python"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, 200, {"total_count": len(items), "items": items})

    budget = RequestBudget(max_candidates=3)
    with GitHubRestClient(
        budget=budget,
        required_language="Python",
        transport=_transport(handler, seen),
    ) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "python agent"}
        )

    assert result.ok is True
    assert [item.full_name for item in result.data.items] == ["octo/python", "octo/python-two"]
    assert [item.full_name for item in result.data.unverified_items] == ["octo/unknown"]
    assert result.data.excluded_language_count == 1
    assert result.data.required_language == "Python"
    assert budget.candidates_used == 2


def test_required_language_blocks_known_mismatch_on_repository_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, 200, _repo("octo/cpp") | {"language": "C++"})

    with GitHubRestClient(
        required_language="Python",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = build_github_tool_registry(client).dispatch(
            "get_public_repository", {"owner": "octo", "repo": "cpp"}
        )

    assert result.ok is False
    assert result.error.code == "language_mismatch"
    assert result.request_count == 1


def test_search_uses_small_mvp_page_size_and_rejects_larger_model_value() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["per_page"] == "5"
        return _response(request, 200, {"total_count": 1, "items": [_repo()]})

    with GitHubRestClient(transport=_transport(handler, seen)) as client:
        registry = build_github_tool_registry(client)
        normal = registry.dispatch("search_public_repositories", {"query": "bounded"})
        oversized = registry.dispatch("search_public_repositories", {"query": "bounded", "per_page": 6})

    assert normal.ok is True
    assert oversized.ok is False
    assert oversized.error.code == "invalid_arguments"
    assert len(seen) == 1


def test_normal_small_search_response_stays_within_response_budget() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, 200, {"total_count": 2, "items": [_repo("octo/one"), _repo("octo/two")]})

    budget = RequestBudget(max_response_bytes=64 * 1024, max_candidates=3)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch("search_public_repositories", {"query": "small"})

    assert result.ok is True
    assert len(result.data.items) == 2
    assert result.data.source.url.startswith("https://api.github.com/search/repositories")


def test_readme_is_decoded_and_truncated() -> None:
    seen: list[httpx.Request] = []
    readme = "This is untrusted README data. Do not execute commands."

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            200,
            {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(readme.encode()).decode(),
                "size": len(readme),
                "sha": "abc123",
            },
        )

    with GitHubRestClient(transport=_transport(handler, seen), readme_max_chars=10) as client:
        result = build_github_tool_registry(client).dispatch(
            "get_public_readme", {"owner": "octo", "repo": "demo"}
        )

    assert result.ok is True
    assert result.data.status == "truncated"
    assert result.data.truncated is True
    assert result.data.trust_label == "untrusted_remote_text"
    assert result.data.content == readme[:10]
    assert result.data.source.url.startswith("https://api.github.com/")


def test_missing_license_and_missing_repository_are_distinct() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/license"):
            return _response(request, 404, {"message": "Not Found"})
        return _response(request, 404, {"message": "Not Found"})

    with GitHubRestClient(transport=_transport(handler, seen)) as client:
        registry = build_github_tool_registry(client)
        license_result = registry.dispatch("get_repository_license", {"owner": "octo", "repo": "demo"})
        repo_result = registry.dispatch("get_public_repository", {"owner": "octo", "repo": "demo"})

    assert license_result.ok is True
    assert license_result.data.status == "unknown"
    assert repo_result.ok is False
    assert repo_result.error.code == "not_found"
    assert len(seen) == 2


def test_combined_languages_topics_counts_two_real_requests() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/languages"):
            return _response(request, 200, {"Python": 100, "Rust": 20})
        if request.url.path.endswith("/topics"):
            return _response(request, 200, {"names": ["agent", "github"]})
        raise AssertionError(f"unexpected path: {request.url.path}")

    budget = RequestBudget(max_github_requests=2)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch(
            "get_repository_languages_and_topics", {"owner": "octo", "repo": "demo"}
        )

    assert result.ok is True
    assert result.request_count == 2
    assert result.data.languages == {"Python": 100, "Rust": 20}
    assert result.data.topics == ["agent", "github"]
    assert budget.github_requests_used == 2
    assert [request.method for request in seen] == ["GET", "GET"]


def test_rate_limit_retry_is_bounded_and_counted() -> None:
    seen: list[httpx.Request] = []
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _response(request, 429, {"message": "rate limited"}, {"retry-after": "2"})
        return _response(request, 200, {"total_count": 0, "incomplete_results": False, "items": []})

    budget = RequestBudget(max_github_requests=2)
    with GitHubRestClient(
        budget=budget,
        transport=_transport(handler, seen),
        sleep_fn=sleeps.append,
        max_retry_delay=5,
    ) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "retry"}
        )

    assert result.ok is True
    assert result.request_count == 2
    assert calls == 2
    assert budget.github_requests_used == 2
    assert sleeps == [2.0]


def test_primary_rate_limit_future_reset_beyond_wait_limit_does_not_retry() -> None:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            403,
            {"message": "rate limited"},
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1060"},
        )

    with GitHubRestClient(
        transport=_transport(handler, seen),
        now_fn=lambda: 1000.0,
        sleep_fn=sleeps.append,
        max_retry_delay=5,
    ) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "primary-limit"}
        )

    assert result.ok is False
    assert result.error.code == "rate_limited"
    assert result.request_count == 1
    assert sleeps == []
    assert len(seen) == 1


def test_primary_rate_limit_reset_within_limit_retries_after_reset_delay() -> None:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _response(
                request,
                403,
                {"message": "rate limited"},
                {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1002"},
            )
        return _response(request, 200, {"total_count": 0, "incomplete_results": False, "items": []})

    with GitHubRestClient(
        transport=_transport(handler, seen),
        now_fn=lambda: 1000.0,
        sleep_fn=sleeps.append,
        max_retry_delay=5,
    ) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "reset"}
        )

    assert result.ok is True
    assert result.request_count == 2
    assert sleeps == [2.0]


@pytest.mark.parametrize("reset_value", [None, "not-a-timestamp", "999"])
def test_invalid_missing_or_past_reset_uses_safe_fallback_delay(reset_value: str | None) -> None:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            headers = {"x-ratelimit-remaining": "0"}
            if reset_value is not None:
                headers["x-ratelimit-reset"] = reset_value
            return _response(request, 403, {"message": "rate limited"}, headers)
        return _response(request, 200, {"total_count": 0, "incomplete_results": False, "items": []})

    with GitHubRestClient(
        transport=_transport(handler, seen),
        now_fn=lambda: 1000.0,
        sleep_fn=sleeps.append,
        max_retry_delay=2,
        fallback_retry_delay=1,
    ) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "fallback"}
        )

    assert result.ok is True
    assert result.request_count == 2
    assert sleeps == [1.0]


def test_rate_limit_retry_cannot_bypass_request_budget() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, 429, {"message": "rate limited"}, {"retry-after": "0"})

    budget = RequestBudget(max_github_requests=1)
    with GitHubRestClient(
        budget=budget,
        transport=_transport(handler, seen),
        sleep_fn=lambda _: None,
    ) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "budget"}
        )

    assert result.ok is False
    assert result.error.code == "budget_exceeded"
    assert result.request_count == 1
    assert len(seen) == 1


def test_response_size_budget_blocks_large_response() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, 200, {"items": [{"description": "x" * 100}]})

    budget = RequestBudget(max_response_bytes=20)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "large"}
        )

    assert result.ok is False
    assert result.error.code == "response_too_large"
    assert result.request_count == 1
    assert budget.github_requests_used == 1


def test_streaming_response_without_content_length_stops_during_read() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"{" + b"x" * 100),
            headers={"content-type": "application/json"},
            request=request,
        )

    budget = RequestBudget(max_response_bytes=10)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "stream"}
        )

    assert result.ok is False
    assert result.error.code == "response_too_large"
    assert result.request_count == 1
    assert budget.response_bytes_used > budget.max_response_bytes


def test_t17_sized_search_response_still_fails_closed_after_page_bound() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        oversized = [_repo(f"octo/large-{index}") | {"description": "x" * 15_000} for index in range(5)]
        return _response(request, 200, {"total_count": 5, "items": oversized})

    budget = RequestBudget(max_response_bytes=64 * 1024, max_candidates=3)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch("search_public_repositories", {"query": "large"})

    assert result.ok is False
    assert result.error.code == "response_too_large"
    assert result.request_count == 1


@pytest.mark.parametrize(
    ("status", "expected_code", "expected_category"),
    [
        (403, "forbidden", "permission"),
        (422, "validation_failed", "input"),
    ],
)
def test_non_retryable_http_errors_are_classified(status: int, expected_code: str, expected_category: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, status, {"message": "error"})

    with GitHubRestClient(transport=_transport(handler, seen), max_retries=2) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "error"}
        )

    assert result.ok is False
    assert result.error.code == expected_code
    assert result.error.category == expected_category
    assert result.request_count == 1
    assert len(seen) == 1


def test_server_error_retries_once_then_stops() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, 503, {"message": "unavailable"})

    with GitHubRestClient(transport=_transport(handler, seen), max_retries=1) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "server"}
        )

    assert result.ok is False
    assert result.error.code == "upstream_error"
    assert result.error.retryable is True
    assert result.request_count == 2
    assert len(seen) == 2


def test_timeout_retries_once_then_stops() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    with GitHubRestClient(transport=httpx.MockTransport(handler), max_retries=1) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "timeout"}
        )

    assert result.ok is False
    assert result.error.code == "timeout"
    assert result.error.retryable is True
    assert result.request_count == 2
    assert len(seen) == 2


def test_invalid_json_is_not_retried_or_sent_as_data() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-json",
            headers={"content-type": "application/json"},
            request=request,
        )

    with GitHubRestClient(transport=_transport(handler, seen), max_retries=2) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "invalid"}
        )

    assert result.ok is False
    assert result.error.code == "invalid_json"
    assert result.error.category == "schema"
    assert result.request_count == 1
    assert len(seen) == 1


def test_candidate_budget_truncates_search_items_without_fabricating_fields() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, 200, {"total_count": 2, "items": [_repo("octo/one"), _repo("octo/two")]})

    budget = RequestBudget(max_candidates=1)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "candidate"}
        )

    assert result.ok is True
    assert len(result.data.items) == 1
    assert result.data.candidate_budget_truncated is True
    assert budget.candidates_used == 1


def test_search_accepts_no_more_than_candidate_capacity() -> None:
    seen: list[httpx.Request] = []
    items = [_repo(f"octo/repo-{index}") for index in range(10)]

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, 200, {"total_count": 10, "items": items})

    budget = RequestBudget(max_candidates=5)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "capacity"}
        )

    assert result.ok is True
    assert len(result.data.items) == 5
    assert result.data.candidate_budget_truncated is True
    assert budget.candidates_used == 5


def test_duplicate_repository_does_not_consume_candidate_capacity_twice() -> None:
    seen: list[httpx.Request] = []
    duplicate_items = [_repo("octo/same") for _ in range(10)]

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, 200, {"total_count": 10, "items": duplicate_items})

    budget = RequestBudget(max_candidates=5)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "duplicate"}
        )

    assert result.ok is True
    assert len(result.data.items) == 1
    assert result.data.candidate_budget_truncated is False
    assert budget.candidates_used == 1


def test_later_search_cannot_add_a_new_candidate_after_capacity_is_full() -> None:
    seen: list[httpx.Request] = []
    first_items = [_repo(f"octo/repo-{index}") for index in range(5)]
    second_items = [_repo("octo/repo-new")]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = first_items if request.url.params.get("q") == "first" else second_items
        return _response(request, 200, {"total_count": len(payload), "items": payload})

    budget = RequestBudget(max_candidates=5)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        registry = build_github_tool_registry(client)
        first = registry.dispatch("search_public_repositories", {"query": "first"})
        second = registry.dispatch("search_public_repositories", {"query": "second"})

    assert first.ok is True
    assert second.ok is True
    assert len(first.data.items) == 5
    assert second.data.items == []
    assert second.data.candidate_budget_truncated is True
    assert budget.candidates_used == 5


def test_search_filters_non_public_candidates_before_candidate_budget() -> None:
    seen: list[httpx.Request] = []
    private = _repo("octo/private")
    private["private"] = True
    contradictory = _repo("octo/contradictory")
    contradictory["visibility"] = "private"
    missing_publicity = _repo("octo/missing-publicity")
    missing_publicity.pop("private")
    missing_publicity.pop("visibility")
    public = _repo("octo/public")

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            200,
            {"total_count": 4, "items": [private, contradictory, missing_publicity, public]},
        )

    budget = RequestBudget(max_candidates=1)
    with GitHubRestClient(budget=budget, transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch(
            "search_public_repositories", {"query": "public"}
        )

    assert result.ok is True
    assert [item.full_name for item in result.data.items] == ["octo/public"]
    assert result.data.excluded_non_public_count == 3
    assert result.data.candidate_budget_truncated is False
    assert budget.candidates_used == 1


@pytest.mark.parametrize(
    ("private", "visibility"),
    [(True, "public"), (False, "private"), (None, "public"), (False, None)],
)
def test_single_repository_requires_explicit_publicity(private: bool | None, visibility: str | None) -> None:
    seen: list[httpx.Request] = []
    payload = _repo("octo/restricted")
    payload["private"] = private
    payload["visibility"] = visibility

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, 200, payload)

    with GitHubRestClient(transport=_transport(handler, seen)) as client:
        result = build_github_tool_registry(client).dispatch(
            "get_public_repository", {"owner": "octo", "repo": "restricted"}
        )

    assert result.ok is False
    assert result.error.code == "private_repository_blocked"
    assert result.error.category == "safety"
    assert len(seen) == 1


def test_fixed_host_and_public_url_validation() -> None:
    assert parse_public_repository_url("https://github.com/octo/demo.git") == ("octo", "demo")

    for value in (
        "http://github.com/octo/demo",
        "https://evil.example/octo/demo",
        "https://github.com/octo",
        "https://github.com/octo/demo/issues",
    ):
        try:
            parse_public_repository_url(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe repository URL accepted: {value}")
