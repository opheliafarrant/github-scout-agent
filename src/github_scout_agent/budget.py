"""Deterministic request, response-size, and candidate budgets."""

from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    """Raised when a program-enforced budget blocks an operation."""

    def __init__(self, dimension: str, limit: int, current: int) -> None:
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
    github_requests_used: int = 0
    response_bytes_used: int = 0
    candidates_used: int = 0
    _dimensions_exhausted: set[str] = field(default_factory=set)

    def reserve_github_request(self) -> None:
        if self.github_requests_used >= self.max_github_requests:
            self._dimensions_exhausted.add("github_requests")
            raise BudgetExceeded(
                "github_requests", self.max_github_requests, self.github_requests_used
            )
        self.github_requests_used += 1

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
        remaining = max(self.max_candidates - self.candidates_used, 0)
        accepted = min(count, remaining)
        self.candidates_used += accepted
        truncated = accepted < count
        if truncated:
            self._dimensions_exhausted.add("candidates")
        return accepted, truncated

    @property
    def exhausted_dimensions(self) -> tuple[str, ...]:
        return tuple(sorted(self._dimensions_exhausted))

    def snapshot(self) -> dict[str, int | tuple[str, ...]]:
        return {
            "github_requests_used": self.github_requests_used,
            "response_bytes_used": self.response_bytes_used,
            "candidates_used": self.candidates_used,
            "max_github_requests": self.max_github_requests,
            "max_response_bytes": self.max_response_bytes,
            "max_candidates": self.max_candidates,
            "exhausted_dimensions": self.exhausted_dimensions,
        }
