"""Deterministic request, response-size, and candidate budgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable, Iterable
import time


class BudgetExceeded(RuntimeError):
    """Raised when a program-enforced budget blocks an operation."""

    def __init__(self, dimension: str, limit: int | float, current: int | float) -> None:
        self.dimension = dimension
        self.limit = limit
        self.current = current
        super().__init__(f"budget exceeded: {dimension} ({current}/{limit})")


@dataclass
class RequestBudget:
    """Small S3-02 budget controller; limits are test/runtime defaults, not PRD constants."""

    max_github_requests: int = 20
    max_response_bytes: int = 64 * 1024
    max_candidates: int = 10
    max_model_calls: int | None = None
    max_task_seconds: float | None = None
    max_feedback_bytes: int | None = None
    max_model_response_bytes: int | None = 64 * 1024
    clock: Callable[[], float] = time.monotonic
    github_requests_used: int = 0
    response_bytes_used: int = 0
    candidates_used: int = 0
    accepted_candidate_ids: set[str] = field(default_factory=set)
    model_calls_used: int = 0
    feedback_bytes_used: int = 0
    model_response_bytes_used: int = 0
    task_started_at: float | None = None
    _dimensions_exhausted: set[str] = field(default_factory=set)

    def start_task(self) -> None:
        if self.task_started_at is None:
            self.task_started_at = self.clock()

    def elapsed_seconds(self) -> float:
        if self.task_started_at is None:
            return 0.0
        return max(self.clock() - self.task_started_at, 0.0)

    def check_task_time(self) -> None:
        if self.max_task_seconds is None:
            return
        elapsed = self.elapsed_seconds()
        if elapsed >= self.max_task_seconds:
            self._dimensions_exhausted.add("task_time")
            raise BudgetExceeded("task_time", self.max_task_seconds, elapsed)

    def reserve_model_call(self) -> None:
        self.start_task()
        self.check_task_time()
        if self.max_model_calls is not None and self.model_calls_used >= self.max_model_calls:
            self._dimensions_exhausted.add("model_calls")
            raise BudgetExceeded("model_calls", self.max_model_calls, self.model_calls_used)
        self.model_calls_used += 1

    def record_feedback_size(self, size: int) -> None:
        if size < 0:
            raise ValueError("feedback size cannot be negative")
        self.feedback_bytes_used += size
        if self.max_feedback_bytes is not None and size > self.max_feedback_bytes:
            self._dimensions_exhausted.add("feedback_bytes")
            raise BudgetExceeded("feedback_bytes", self.max_feedback_bytes, size)

    def record_model_response_chunk(self, size: int, response_size_so_far: int) -> None:
        if size < 0 or response_size_so_far < 0:
            raise ValueError("model response size cannot be negative")
        self.model_response_bytes_used += size
        if self.max_model_response_bytes is not None and response_size_so_far > self.max_model_response_bytes:
            self._dimensions_exhausted.add("model_response_bytes")
            raise BudgetExceeded("model_response_bytes", self.max_model_response_bytes, response_size_so_far)

    def reserve_github_request(self) -> None:
        self.start_task()
        self.check_task_time()
        if self.github_requests_used >= self.max_github_requests:
            self._dimensions_exhausted.add("github_requests")
            raise BudgetExceeded(
                "github_requests", self.max_github_requests, self.github_requests_used
            )
        self.github_requests_used += 1

    @property
    def github_requests_remaining(self) -> int:
        return max(self.max_github_requests - self.github_requests_used, 0)

    def record_response_size(self, size: int) -> None:
        self.record_response_chunk(size, size)

    def record_response_chunk(self, size: int, response_size_so_far: int) -> None:
        if size < 0 or response_size_so_far < 0:
            raise ValueError("response size cannot be negative")
        self.response_bytes_used += size
        if response_size_so_far > self.max_response_bytes:
            self._dimensions_exhausted.add("response_bytes")
            raise BudgetExceeded("response_bytes", self.max_response_bytes, response_size_so_far)

    def take_candidates(self, count: int) -> tuple[int, bool]:
        if count < 0:
            raise ValueError("candidate count cannot be negative")
        anonymous_ids = [f"__anonymous_candidate_{self.candidates_used + index}" for index in range(count)]
        accepted_ids, truncated = self.take_candidate_ids(anonymous_ids)
        return len(accepted_ids), truncated

    def take_candidate_ids(self, candidate_ids: Iterable[str]) -> tuple[list[str], bool]:
        """Accept unique candidate identities without counting repeats twice."""

        ordered_unique: list[str] = []
        seen: set[str] = set()
        for candidate_id in candidate_ids:
            normalized = candidate_id.casefold()
            if normalized and normalized not in seen:
                seen.add(normalized)
                ordered_unique.append(normalized)

        new_ids = [candidate_id for candidate_id in ordered_unique if candidate_id not in self.accepted_candidate_ids]
        remaining = max(self.max_candidates - self.candidates_used, 0)
        accepted_new = new_ids[:remaining]
        self.accepted_candidate_ids.update(accepted_new)
        self.candidates_used += len(accepted_new)
        truncated = len(accepted_new) < len(new_ids)
        if truncated:
            self._dimensions_exhausted.add("candidates")
        accepted = [candidate_id for candidate_id in ordered_unique if candidate_id in self.accepted_candidate_ids]
        return accepted, truncated

    def is_candidate_accepted(self, candidate_id: str) -> bool:
        return candidate_id.casefold() in self.accepted_candidate_ids

    @property
    def candidates_full(self) -> bool:
        return self.candidates_used >= self.max_candidates

    @property
    def exhausted_dimensions(self) -> tuple[str, ...]:
        return tuple(sorted(self._dimensions_exhausted))

    @property
    def fatal_exhausted_dimensions(self) -> tuple[str, ...]:
        return tuple(sorted(dimension for dimension in self._dimensions_exhausted if dimension != "candidates"))

    def snapshot(self) -> dict[str, object]:
        return {
            "github_requests_used": self.github_requests_used,
            "response_bytes_used": self.response_bytes_used,
            "candidates_used": self.candidates_used,
            "accepted_candidate_count": len(self.accepted_candidate_ids),
            "model_calls_used": self.model_calls_used,
            "feedback_bytes_used": self.feedback_bytes_used,
            "model_response_bytes_used": self.model_response_bytes_used,
            "elapsed_seconds": self.elapsed_seconds(),
            "max_github_requests": self.max_github_requests,
            "max_response_bytes": self.max_response_bytes,
            "max_candidates": self.max_candidates,
            "max_model_calls": self.max_model_calls,
            "max_task_seconds": self.max_task_seconds,
            "max_feedback_bytes": self.max_feedback_bytes,
            "max_model_response_bytes": self.max_model_response_bytes,
            "exhausted_dimensions": self.exhausted_dimensions,
            "fatal_exhausted_dimensions": self.fatal_exhausted_dimensions,
        }
