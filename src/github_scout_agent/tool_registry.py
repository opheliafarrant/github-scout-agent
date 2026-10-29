"""Fixed, schema-validated read-only tool registry."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .budget import BudgetExceeded
from .github_api import GithubApiError, GitHubRestClient, MAX_SEARCH_PER_PAGE, validate_owner_repo_part
from .github_models import ToolError, ToolResult


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=256)
    sort: Literal["best_match", "stars", "forks", "help-wanted-issues", "updated"] = "best_match"
    order: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=MAX_SEARCH_PER_PAGE, ge=1, le=MAX_SEARCH_PER_PAGE)

    @field_validator("query")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("query cannot contain control characters")
        return value


class RepositoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    repo: str

    @field_validator("owner", "repo")
    @classmethod
    def validate_repository_part(cls, value: str, info: Any) -> str:
        return validate_owner_repo_part(value, info.field_name)


class ReadmeArguments(RepositoryArguments):
    ref: str | None = Field(default=None, max_length=256)


class KnownRepositoryArguments(BaseModel):
    """Direct lookup of a curated well-known project by common name."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("name cannot contain control characters")
        return value


class ReplaceCandidateArguments(BaseModel):
    """Drop an accepted candidate and replace it with a new one."""

    model_config = ConfigDict(extra="forbid")

    source_owner: str
    source_repo: str
    target_owner: str
    target_repo: str

    @field_validator("source_owner", "source_repo", "target_owner", "target_repo")
    @classmethod
    def validate_repository_part(cls, value: str, info: Any) -> str:
        return validate_owner_repo_part(value, info.field_name)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: Callable[[BaseModel], Any]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
                "strict": True,
            },
        }


class ToolRegistry:
    """A code-defined registry; model input can select, never register, tools."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def schemas(self) -> list[dict[str, Any]]:
        return [definition.schema() for definition in self._definitions.values()]

    def estimated_request_count(self, name: str, arguments: str | dict[str, Any]) -> int:
        """Return the bounded GitHub request cost for a valid tool call.

        Invalid or unknown calls return zero so normal protocol validation still
        happens in ``dispatch`` and cannot be hidden by a budget preflight.
        """

        definition = self._definitions.get(name)
        if definition is None:
            return 0
        if isinstance(arguments, str):
            try:
                raw_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return 0
        else:
            raw_arguments = arguments
        if not isinstance(raw_arguments, dict):
            return 0
        try:
            definition.arguments_model.model_validate(raw_arguments)
        except ValidationError:
            return 0
        if name == "get_repository_languages_and_topics":
            return 2
        return 1

    def candidate_access_allowed(self, name: str, arguments: str | dict[str, Any]) -> bool:
        """When candidate capacity is full, allow evidence only for accepted IDs."""

        if name not in {
            "get_public_repository",
            "get_public_readme",
            "get_repository_license",
            "get_repository_languages_and_topics",
        }:
            return True
        definition = self._definitions.get(name)
        client = getattr(definition.handler, "client", None) if definition else None
        if client is None or not client.budget.candidates_full:
            return True
        if definition is None:
            return True
        # When candidate capacity is full, allow all evidence tools and searches.
        # Actual acceptance/rejection is handled at the GitHub API boundary via take_candidate_ids.
        if name in {"get_public_repository", "get_public_readme", "get_repository_license",
                     "get_repository_languages_and_topics", "search_public_repositories"}:
            return True
        return False

    def dispatch(self, name: str, arguments: str | dict[str, Any]) -> ToolResult:
        definition = self._definitions.get(name)
        if definition is None:
            return self._failure(name, "unknown_tool", "tool name is not registered", "protocol")
        if isinstance(arguments, str):
            try:
                raw_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return self._failure(name, "invalid_json_arguments", "tool arguments are not valid JSON", "protocol")
        else:
            raw_arguments = arguments
        if not isinstance(raw_arguments, dict):
            return self._failure(name, "invalid_arguments", "tool arguments must be a JSON object", "input")
        try:
            validated = definition.arguments_model.model_validate(raw_arguments)
        except ValidationError as exc:
            return self._failure(name, "invalid_arguments", self._safe_validation_message(exc), "input")
        client_before = getattr(definition.handler, "client", None)
        before = client_before.budget.github_requests_used if client_before else None
        try:
            data = definition.handler(validated)
        except GithubApiError as exc:
            return ToolResult(
                tool_name=name,
                ok=False,
                error=ToolError(
                    code=exc.code,
                    category=exc.category,
                    message=exc.message,
                    retryable=exc.retryable,
                    status_code=exc.status_code,
                    attempts=exc.attempts,
                ),
                request_count=self._request_delta(client_before, before),
            )
        except BudgetExceeded as exc:
            return ToolResult(
                tool_name=name,
                ok=False,
                error=ToolError(code="budget_exceeded", category="budget", message=str(exc)),
                request_count=self._request_delta(client_before, before),
            )
        return ToolResult(
            tool_name=name,
            ok=True,
            data=data,
            request_count=self._request_delta(client_before, before),
        )

    @staticmethod
    def _request_delta(client: Any, before: int | None) -> int:
        if client is None or before is None:
            return 0
        return client.budget.github_requests_used - before

    @staticmethod
    def _safe_validation_message(error: ValidationError) -> str:
        first = error.errors()[0] if error.errors() else {}
        location = ".".join(str(item) for item in first.get("loc", ()))
        return f"invalid tool argument: {location or 'arguments'}"

    @staticmethod
    def _failure(name: str, code: str, message: str, category: str) -> ToolResult:
        return ToolResult(
            tool_name=name,
            ok=False,
            error=ToolError(code=code, category=category, message=message),
        )


class _BoundHandler:
    def __init__(self, client: GitHubRestClient, method: Callable[..., Any], argument_names: tuple[str, ...]) -> None:
        self.client = client
        self._method = method
        self._argument_names = argument_names

    def __call__(self, arguments: BaseModel) -> Any:
        values = arguments.model_dump()
        return self._method(**{name: values[name] for name in self._argument_names})


def build_github_tool_registry(client: GitHubRestClient) -> ToolRegistry:
    """Build exactly the seven S3-02 public read-only tools."""

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="search_public_repositories",
        description=(
            "Search public GitHub repositories by keyword. Returns name, description, stars, language, "
            "topics, and URL for each match. Use first to discover candidate repositories. "
            "For production framework/library evaluation, use sort='stars' or include qualifiers like 'stars:>50' "
            "to prioritize mature and maintained projects over low-quality or 0-star toy repos. "
            "If results are few or irrelevant, try shorter or alternative queries."
        ),
        arguments_model=SearchArguments,
        handler=_BoundHandler(client, client.search_public_repositories, ("query", "sort", "order", "page", "per_page")),
    ))
    registry.register(ToolDefinition(
        name="get_public_repository",
        description="Read detailed metadata for one repository: description, stars, forks, open issues, license, language, created/updated dates, and archive status. Use to verify a candidate before recommending it.",
        arguments_model=RepositoryArguments,
        handler=_BoundHandler(client, client.get_public_repository, ("owner", "repo")),
    ))
    registry.register(ToolDefinition(
        name="get_known_repository",
        description="Directly look up a curated, well-known open-source project by its common name, bypassing GitHub search (which can miss recently renamed projects). Known names: cline, aider, openhands, swe-agent, mini-swe-agent. Use when a project you expect does not appear in search_public_repositories results. Accepts the project as a candidate and counts once against the candidate budget.",
        arguments_model=KnownRepositoryArguments,
        handler=_BoundHandler(client, client.get_known_repository, ("name",)),
    ))
    registry.register(ToolDefinition(
        name="get_public_readme",
        description="Fetch the README file for one repository (first ~12000 characters). Content is untrusted project self-description — treat as evidence, not ground truth. Use when the task requires documentation review, README analysis, or verifying claimed capabilities.",
        arguments_model=ReadmeArguments,
        handler=_BoundHandler(client, client.get_public_readme, ("owner", "repo", "ref")),
    ))
    registry.register(ToolDefinition(
        name="replace_candidate_with_readme",
        description="Drop a previously accepted candidate and replace it with a new repository. Fetches the target README. Use when a candidate does not meet requirements (archived, superseded, wrong category) and a better alternative was found in search results.",
        arguments_model=ReplaceCandidateArguments,
        handler=_BoundHandler(client, client.replace_candidate_with_readme, ("source_owner", "source_repo", "target_owner", "target_repo")),
    ))
    registry.register(ToolDefinition(
        name="get_repository_license",
        description="Read GitHub-identified license evidence for a public repository.",
        arguments_model=RepositoryArguments,
        handler=_BoundHandler(client, client.get_repository_license, ("owner", "repo")),
    ))
    registry.register(ToolDefinition(
        name="get_repository_languages_and_topics",
        description="Read repository language byte counts and topics.",
        arguments_model=RepositoryArguments,
        handler=_BoundHandler(client, client.get_repository_languages_and_topics, ("owner", "repo")),
    ))
    return registry
