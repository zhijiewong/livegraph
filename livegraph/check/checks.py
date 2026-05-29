"""Per-check adapters: wrap Phase 10/11 functions into CheckResult."""
from __future__ import annotations

import fnmatch

from livegraph.check.config import (
    ChurnConfig, CyclesConfig, HubsConfig, LayeringConfig,
)
from livegraph.check.models import CheckResult
from livegraph.graph.backend import GraphBackend
from livegraph.mcp.tools_architecture import (
    find_cycles, hubs as hubs_tool, layering_violations,
)
from livegraph.mcp.tools_history import top_churn


def _matches_any(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def check_cycles(
    cfg: CyclesConfig, backend: GraphBackend, project: str,
) -> CheckResult:
    if not cfg.enabled:
        return CheckResult.skipped("cycles", "disabled in config")
    out = find_cycles(
        backend, project, scope=cfg.scope, provenance=cfg.provenance,
        min_size=cfg.min_size,
        limit=max(cfg.max_cycles + 10, 20),
    )
    if out["warning"]:
        return CheckResult.error("cycles", out["warning"])
    cycles = list(out["cycles"])
    actual = len(cycles)
    status = "passed" if actual <= cfg.max_cycles else "failed"
    return CheckResult(
        check="cycles", status=status,
        actual=actual, threshold=cfg.max_cycles,
        items=tuple(cycles),
    )


def check_layering(
    cfg: LayeringConfig, backend: GraphBackend, project: str,
) -> CheckResult:
    if not cfg.enabled:
        return CheckResult.skipped("layering", "disabled in config")
    if not cfg.layers:
        return CheckResult.error(
            "layering", "no layers defined in config",
        )
    out = layering_violations(
        backend, project,
        layers=[dict(layer) for layer in cfg.layers],
        edge_kind=cfg.edge_kind,
        limit=max(cfg.max_violations + 50, 50),
    )
    if out.get("warning"):
        return CheckResult.error("layering", out["warning"])
    violations = list(out["violations"])
    actual = len(violations)
    status = "passed" if actual <= cfg.max_violations else "failed"
    return CheckResult(
        check="layering", status=status,
        actual=actual, threshold=cfg.max_violations,
        items=tuple(violations),
    )


def check_churn(
    cfg: ChurnConfig, backend: GraphBackend, project: str,
) -> CheckResult:
    if not cfg.enabled:
        return CheckResult.skipped("churn", "disabled in config")
    out = top_churn(
        backend, project,
        window_days=cfg.window_days, limit=100, kind="any",
    )
    if out.get("warning"):
        return CheckResult.error("churn", out["warning"])
    hotspots = []
    for r in out["results"]:
        if r.get("commit_count", 0) <= cfg.hot_files_threshold:
            continue
        file = r.get("file") or ""
        if _matches_any(file, cfg.ignore):
            continue
        hotspots.append(r)
    actual = len(hotspots)
    status = "passed" if actual == 0 else "failed"
    return CheckResult(
        check="churn", status=status,
        actual=actual, threshold=cfg.hot_files_threshold,
        items=tuple(hotspots),
    )


def check_hubs(
    cfg: HubsConfig, backend: GraphBackend, project: str,
) -> CheckResult:
    if not cfg.enabled:
        return CheckResult.skipped("hubs", "disabled in config")
    out = hubs_tool(
        backend, project,
        kind=cfg.kind, min_fanin=cfg.min_fanin,
        limit=max(cfg.max_hubs + 10, 20),
    )
    if out.get("warning"):
        return CheckResult.error("hubs", out["warning"])
    results = list(out["results"])
    actual = len(results)
    status = "passed" if actual <= cfg.max_hubs else "failed"
    return CheckResult(
        check="hubs", status=status,
        actual=actual, threshold=cfg.max_hubs,
        items=tuple(results),
    )
