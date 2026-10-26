"""Explicit S4-01 live GitHub REST verification.

Run only with: python -m pytest -m live_github -s
The default pytest command excludes this module's marker.
"""

from __future__ import annotations

import json
import os

import pytest

from github_scout_agent.budget import RequestBudget
from github_scout_agent.github_api import GitHubRestClient
from github_scout_agent.tool_registry import build_github_tool_registry


def _error_summary(result):
    if result.ok:
        return None
    return result.error.model_dump() if result.error else {"code": "unknown"}


@pytest.mark.live_github
def test_live_public_github_readonly_contract() -> None:
    token = os.environ.get("GITHUB_TOKEN") or None
    budget = RequestBudget(
        max_github_requests=8,
        max_response_bytes=256 * 1024,
        max_candidates=5,
    )
    with GitHubRestClient(token=token, budget=budget, timeout=10.0, max_retries=1, max_retry_delay=2.0) as client:
        registry = build_github_tool_registry(client)
        results = {
            "search": registry.dispatch(
                "search_public_repositories",
                {"query": "repo:octocat/Hello-World is:public"},
            ),
            "repository": registry.dispatch(
                "get_public_repository", {"owner": "octocat", "repo": "Hello-World"}
            ),
            "readme": registry.dispatch(
                "get_public_readme", {"owner": "octocat", "repo": "Hello-World"}
            ),
            "license": registry.dispatch(
                "get_repository_license", {"owner": "octocat", "repo": "Hello-World"}
            ),
            "languages_topics": registry.dispatch(
                "get_repository_languages_and_topics", {"owner": "octocat", "repo": "Hello-World"}
            ),
        }

        summary = {
            "auth_mode": "readonly_token" if token else "anonymous",
            "budget": budget.snapshot(),
            "results": {
                "search": {
                    "ok": results["search"].ok,
                    "error": _error_summary(results["search"]),
                    "items": len(results["search"].data.items) if results["search"].ok else None,
                    "incomplete_results": results["search"].data.incomplete_results if results["search"].ok else None,
                    "excluded_non_public": results["search"].data.excluded_non_public_count if results["search"].ok else None,
                },
                "repository": {
                    "ok": results["repository"].ok,
                    "error": _error_summary(results["repository"]),
                    "full_name": results["repository"].data.full_name if results["repository"].ok else None,
                    "is_public": results["repository"].data.is_public if results["repository"].ok else None,
                    "archived": results["repository"].data.is_archived if results["repository"].ok else None,
                    "missing_fields": results["repository"].data.missing_fields if results["repository"].ok else None,
                },
                "readme": {
                    "ok": results["readme"].ok,
                    "error": _error_summary(results["readme"]),
                    "status": results["readme"].data.status if results["readme"].ok else None,
                    "truncated": results["readme"].data.truncated if results["readme"].ok else None,
                    "trust_label": results["readme"].data.trust_label if results["readme"].ok else None,
                },
                "license": {
                    "ok": results["license"].ok,
                    "error": _error_summary(results["license"]),
                    "status": results["license"].data.status if results["license"].ok else None,
                },
                "languages_topics": {
                    "ok": results["languages_topics"].ok,
                    "error": _error_summary(results["languages_topics"]),
                    "language_count": len(results["languages_topics"].data.languages) if results["languages_topics"].ok else None,
                    "topic_count": len(results["languages_topics"].data.topics) if results["languages_topics"].ok else None,
                },
            },
            "request_log": client.request_log,
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))

        for name, result in results.items():
            assert result.ok, f"{name} failed: {_error_summary(result)}"
        assert results["repository"].data.is_public is True
        assert results["repository"].data.html_url.startswith("https://github.com/")
        assert all(entry["method"] == "GET" for entry in client.request_log)
        assert all(entry["host"] == "api.github.com" for entry in client.request_log)
        assert budget.github_requests_used == len(client.request_log)
        assert budget.github_requests_used <= budget.max_github_requests
