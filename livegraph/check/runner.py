"""Orchestrator + exit-code logic for `livegraph check`."""
from __future__ import annotations

from livegraph.check.checks import (
    check_churn, check_cycles, check_hubs, check_layering,
)
from livegraph.check.config import CheckConfig
from livegraph.check.models import CheckResult, Report, StalenessReport
from livegraph.graph.backend import GraphBackend


def run_checks(
    config: CheckConfig,
    backend: GraphBackend,
    staleness: StalenessReport,
    fail_fast: bool = False,
) -> Report:
    """Run every check in a fixed order; honor fail_fast."""
    results: list[CheckResult] = []

    runners = [
        ("cycles", lambda: check_cycles(config.cycles, backend, config.project)),
        ("layering", lambda: check_layering(config.layering, backend, config.project)),
        ("churn", lambda: check_churn(config.churn, backend, config.project)),
        ("hubs", lambda: check_hubs(config.hubs, backend, config.project)),
    ]
    for _, fn in runners:
        r = fn()
        results.append(r)
        if fail_fast and r.status in ("failed", "error"):
            break

    return Report(
        project=config.project,
        staleness=staleness,
        results=tuple(results),
        warnings=config.warnings,
    )


def compute_exit_code(
    results: tuple[CheckResult, ...],
    staleness: StalenessReport,
    strict: bool,
) -> int:
    """0 = all passed, 1 = at least one failed/error, 2 = strict+stale."""
    if strict and staleness.has_drift:
        return 2
    if any(r.status in ("failed", "error") for r in results):
        return 1
    return 0
