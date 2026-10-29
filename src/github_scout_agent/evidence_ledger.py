"""Single runtime account of what this session actually fetched.

Each gate used to re-derive evidence from the same tool results in its own way:
the finalize prompt, the completeness check, the maturity audit and the report
renderer walked the records separately and disagreed about who counted as
investigated. That divergence is how a report could recommend, and even label
"Verified Match", a repository the tool layer never touched.

The ledger records every repository once, at the moment its evidence arrives, in
the only place that knows what a tool result means.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

README_FETCHED_STATUSES = frozenset({"available", "truncated"})
README_TOOLS = frozenset({"get_public_readme", "replace_candidate_with_readme"})
METADATA_TOOLS = frozenset(
    {
        "get_public_repository",
        "get_repository_license",
        "get_repository_languages_and_topics",
    }
)
_SEARCH_TOOL = "search_public_repositories"


def iso_date(value: Any) -> str:
    """Render a date or datetime as an ISO day, or the value unchanged."""

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class RepoEvidence(BaseModel):
    """What the tools returned about one repository, and whether they were asked."""

    model_config = ConfigDict(extra="forbid")

    full_name: str
    metadata_at: datetime | None = None
    seen_in_search: bool = False
    declared_investigated: bool = False
    readme_requested: bool = False
    readme_status: str | None = None
    stars: int | None = None
    created_at: datetime | None = None
    pushed_at: datetime | None = None
    archived: bool | None = None
    license: str | None = None
    language: str | None = None

    @property
    def readme_fetched(self) -> bool:
        return self.readme_status in README_FETCHED_STATUSES

    @property
    def investigated(self) -> bool:
        """True once a tool read this repository itself, not just a search page.

        A search hit is a hint, not evidence: it carries no README and no proof
        the project does what the query asked for.
        """

        return (
            self.declared_investigated
            or self.metadata_at is not None
            or self.readme_requested
        )

    def describe(self) -> str:
        """One prompt/report line: name plus the facts tools actually returned."""

        bits: list[str] = []
        if self.stars is not None:
            bits.append(f"stars={self.stars}")
        if self.created_at is not None:
            bits.append(f"created={iso_date(self.created_at)}")
        if self.readme_status is not None:
            bits.append(f"readme={self.readme_status}")
        elif self.metadata_at is not None:
            bits.append("readme=not requested")
        return f"{self.full_name} ({', '.join(bits)})" if bits else self.full_name


class EvidenceLedger(BaseModel):
    """Casefolded owner/repo to everything the tools revealed about it."""

    model_config = ConfigDict(extra="forbid")

    repositories: dict[str, RepoEvidence] = Field(default_factory=dict)

    # -- recording -----------------------------------------------------

    def entry(self, full_name: str) -> RepoEvidence:
        key = full_name.strip().casefold()
        current = self.repositories.get(key)
        if current is None:
            current = RepoEvidence(full_name=full_name.strip())
            self.repositories[key] = current
        return current

    def mark_investigated(self, full_name: str) -> None:
        """Trust a result that already declared this repository investigated."""

        self.entry(full_name).declared_investigated = True

    def absorb_repository(
        self,
        record: Any,
        *,
        at: datetime | None = None,
        via_search: bool = False,
    ) -> None:
        """Record one RepositoryRecord-shaped object (search hit or direct read)."""

        name = getattr(record, "full_name", None)
        if not isinstance(name, str) or "/" not in name:
            return
        entry = self.entry(name)
        if via_search:
            entry.seen_in_search = True
        elif entry.metadata_at is None:
            entry.metadata_at = at or datetime.now(timezone.utc)
        stars = getattr(record, "stargazers_count", None)
        if isinstance(stars, int) and stars >= 0:
            entry.stars = stars
        for attribute, target in (("created_at", "created_at"), ("pushed_at", "pushed_at")):
            value = getattr(record, attribute, None)
            if isinstance(value, datetime):
                setattr(entry, target, value)
        archived = getattr(record, "is_archived", None)
        if isinstance(archived, bool):
            entry.archived = archived
        language = getattr(record, "language", None)
        if isinstance(language, str) and language:
            entry.language = language
        licence = getattr(record, "license", None)
        if licence is not None:
            entry.license = (
                getattr(licence, "spdx_id", None)
                or getattr(licence, "name", None)
                or getattr(licence, "key", None)
            )
        readme = getattr(record, "readme", None)
        status = getattr(readme, "status", None)
        if isinstance(status, str) and status != "unknown":
            entry.readme_requested = True
            entry.readme_status = status

    def record_readme_request(self, full_name: str, data: Any, ok: bool) -> None:
        """A README was asked for, whatever the API answered."""

        entry = self.entry(full_name)
        entry.readme_requested = True
        if not ok:
            return
        status = getattr(data, "status", None)
        if isinstance(status, str) and status != "unknown":
            entry.readme_status = status
        elif getattr(data, "full_name", None):
            # replace_candidate_with_readme returns the target repository record.
            self.absorb_repository(data)

    # -- reading -------------------------------------------------------

    def get(self, slug: str) -> RepoEvidence | None:
        return self.repositories.get(slug.strip().casefold())

    def resolve(self, token: str) -> RepoEvidence | None:
        """Map a full name or a bare project name onto a recorded repository."""

        key = token.strip().casefold()
        if not key:
            return None
        direct = self.repositories.get(key)
        if direct is not None:
            return direct
        for entry in self.repositories.values():
            if entry.full_name.rsplit("/", 1)[-1].casefold() == key:
                return entry
        return None

    def known(self) -> tuple[str, ...]:
        """Every repository this session holds tool evidence about, read or not."""

        return tuple(
            entry.full_name for entry in self.repositories.values() if entry.full_name
        )

    def investigated(self) -> tuple[str, ...]:
        return tuple(
            sorted(entry.full_name for entry in self.repositories.values() if entry.investigated)
        )

    def readme_attempted(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                key for key, entry in self.repositories.items() if entry.readme_requested
            )
        )

    def readme_fetched(self) -> bool:
        return any(entry.readme_fetched for entry in self.repositories.values())

    def readme_status(self, slug: str) -> str | None:
        entry = self.get(slug)
        return entry.readme_status if entry is not None else None

    def maturity_metadata(self) -> dict[str, dict[str, Any]]:
        """The shape audit_maturity grades: stars and creation date per repository."""

        return {
            key: {
                "name": entry.full_name,
                "stars": entry.stars,
                "created_at": entry.created_at,
                "pushed_at": entry.pushed_at,
            }
            for key, entry in self.repositories.items()
        }

    def whitelist(self) -> list[str]:
        """Prompt lines for the finalize stage, in stable order."""

        return [
            entry.describe()
            for entry in sorted(
                self.repositories.values(),
                key=lambda item: item.full_name.casefold(),
            )
            if entry.investigated
        ]

    def for_report(self) -> list[RepoEvidence]:
        return [
            entry
            for entry in sorted(
                self.repositories.values(),
                key=lambda item: item.full_name.casefold(),
            )
            if entry.investigated or entry.metadata_at is not None
        ]


def build_ledger(
    tool_results: Iterable[Any] = (),
    executed_calls: Iterable[tuple[Any, Any]] = (),
    candidate_ids: Iterable[str] = (),
) -> EvidenceLedger:
    """Reconstruct the session ledger from tool results and the calls behind them."""

    ledger = EvidenceLedger()
    for candidate_id in candidate_ids:
        name = str(candidate_id or "")
        if name and not name.startswith("__anonymous_"):
            # A candidate only enters the budget after the run accepted it,
            # which requires talking to a tool about it.
            ledger.mark_investigated(name)
    for result in tool_results:
        if not getattr(result, "ok", False):
            continue
        name = getattr(result, "tool_name", None)
        data = getattr(result, "data", None)
        if name == _SEARCH_TOOL:
            for item in getattr(data, "items", None) or []:
                ledger.absorb_repository(item, via_search=True)
        elif name in METADATA_TOOLS or name is None:
            ledger.absorb_repository(data)
    for call, result in executed_calls:
        call_name = getattr(call, "name", None)
        if call_name not in README_TOOLS:
            continue
        try:
            arguments = json.loads(getattr(call, "arguments", "") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(arguments, dict):
            continue
        if call_name == "replace_candidate_with_readme":
            owner, repo = arguments.get("target_owner"), arguments.get("target_repo")
        else:
            owner, repo = arguments.get("owner"), arguments.get("repo")
        if owner and repo:
            ledger.record_readme_request(
                f"{owner}/{repo}", getattr(result, "data", None), bool(getattr(result, "ok", False))
            )
    return ledger
