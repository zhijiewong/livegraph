"""Data classes for the `livegraph check` CI mode."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CheckStatus = Literal["passed", "failed", "skipped", "error"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one check.

    - `passed` / `failed`: threshold compared with `actual`.
    - `skipped`: check disabled in config (reason in `reason`).
    - `error`: check could not run (e.g. underlying tool returned a
      warning). The reason is in `reason`. Counts as a failure for
      exit-code purposes.
    """

    check: str
    status: CheckStatus
    actual: int = 0
    threshold: int = 0
    items: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    reason: str | None = None

    @classmethod
    def skipped(cls, check: str, reason: str) -> "CheckResult":
        return cls(check=check, status="skipped", reason=reason)

    @classmethod
    def error(cls, check: str, reason: str) -> "CheckResult":
        return cls(check=check, status="error", reason=reason)


@dataclass(frozen=True, slots=True)
class StalenessReport:
    """Result of comparing on-disk file hashes to stored content_hash."""

    drifted_files: int

    @property
    def has_drift(self) -> bool:
        return self.drifted_files > 0


@dataclass(frozen=True, slots=True)
class Report:
    """The full check run's output."""

    project: str
    staleness: StalenessReport
    results: tuple[CheckResult, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results
                   if r.status in ("failed", "error"))

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")
