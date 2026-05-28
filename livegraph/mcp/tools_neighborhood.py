"""semantic_neighborhood — vector seeds + per-seed graph expansion.

Lives in its own module so tools.py doesn't keep growing. The seed step
is shared with `semantic_search` via `_semantic_seeds` in `tools.py`.
"""
from __future__ import annotations

from typing import Any

from livegraph.graph.backend import GraphBackend
from livegraph.mcp.tools import _embedded_count, _semantic_seeds
from livegraph.semantic.provider import (
    EmbeddingExtraMissing, EmbeddingProvider,
)

_VALID_KINDS = ("any", "function", "method")
_VALID_INCLUDE = ("callers", "callees", "tests")
_MAX_LIMIT = 50
_MAX_PER_SEED = 50

_CALLERS_CYPHER = (
    "UNWIND $qns AS qn "
    "MATCH (target {qualified_name: qn}) "
    "MATCH (caller)-[r:CALLS]->(target) "
    "WITH qn, caller, collect(DISTINCT r.provenance) AS provs "
    "WITH qn, caller.qualified_name AS neighbor_qn, "
    "     CASE WHEN size(provs) > 1 THEN 'both' ELSE provs[0] END "
    "         AS provenance "
    "ORDER BY qn, neighbor_qn "
    "WITH qn, collect({qualified_name: neighbor_qn, "
    "                  provenance: provenance})[..$per_seed_limit] AS rows "
    "UNWIND rows AS row "
    "RETURN qn AS seed_qn, "
    "       row.qualified_name AS qualified_name, "
    "       row.provenance AS provenance"
)

_CALLEES_CYPHER = (
    "UNWIND $qns AS qn "
    "MATCH (target {qualified_name: qn}) "
    "MATCH (target)-[r:CALLS]->(callee) "
    "WITH qn, callee, collect(DISTINCT r.provenance) AS provs "
    "WITH qn, callee.qualified_name AS neighbor_qn, "
    "     CASE WHEN size(provs) > 1 THEN 'both' ELSE provs[0] END "
    "         AS provenance "
    "ORDER BY qn, neighbor_qn "
    "WITH qn, collect({qualified_name: neighbor_qn, "
    "                  provenance: provenance})[..$per_seed_limit] AS rows "
    "UNWIND rows AS row "
    "RETURN qn AS seed_qn, "
    "       row.qualified_name AS qualified_name, "
    "       row.provenance AS provenance"
)

_TESTS_CYPHER = (
    "UNWIND $qns AS qn "
    "MATCH (target {qualified_name: qn}) "
    "MATCH (test:Test)-[:COVERS]->(target) "
    "WITH qn, test.qualified_name AS neighbor_qn "
    "ORDER BY qn, neighbor_qn "
    "WITH qn, collect(neighbor_qn)[..$per_seed_limit] AS rows "
    "UNWIND rows AS neighbor_qn "
    "RETURN qn AS seed_qn, neighbor_qn AS qualified_name"
)


def _normalize_include(include):
    if include is None:
        return list(_VALID_INCLUDE)
    valid = [k for k in include if k in _VALID_INCLUDE]
    return valid if valid else list(_VALID_INCLUDE)


def _expansion_rows(
    backend: GraphBackend, cypher: str, qns: list[str], per_seed_limit: int,
) -> list[dict[str, Any]]:
    if not qns:
        return []
    return backend.execute(cypher, qns=qns, per_seed_limit=per_seed_limit)


def semantic_neighborhood(
    backend: GraphBackend,
    project: str,
    provider: EmbeddingProvider,
    query: str,
    limit: int = 10,
    per_seed_limit: int = 10,
    kind: str = "any",
    include: list[str] | None = None,
    min_score: float = 0.0,
) -> dict[str, Any]:
    """Vector seeds + per-seed callers/callees/tests in one call."""
    limit = max(1, min(int(limit), _MAX_LIMIT))
    per_seed_limit = max(1, min(int(per_seed_limit), _MAX_PER_SEED))
    include_n = _normalize_include(include)

    try:
        seed_result = _semantic_seeds(
            backend, project, provider, query, limit, kind, min_score,
        )
    except EmbeddingExtraMissing as exc:
        return {
            "results": [],
            "model": getattr(provider, "name", None),
            "embedded_count": 0,
            "warning": (
                f"semantic search unavailable: {exc} "
                f"(install with `pip install 'livegraph[semantic]'`)"
            ),
        }

    if not seed_result["ok"]:
        return {
            "results": [],
            "model": provider.name,
            "embedded_count": 0,
            "warning": seed_result["warning"],
        }

    seeds = seed_result["seeds"]
    qns = [s.get("qualified_name") for s in seeds
           if s.get("qualified_name") is not None]

    callers_by_seed: dict[str, list[dict[str, Any]]] = {}
    callees_by_seed: dict[str, list[dict[str, Any]]] = {}
    tests_by_seed: dict[str, list[dict[str, Any]]] = {}

    if "callers" in include_n:
        for row in _expansion_rows(
            backend, _CALLERS_CYPHER, qns, per_seed_limit,
        ):
            seed_qn = row.get("seed_qn")
            if seed_qn is None or row.get("qualified_name") is None:
                continue
            callers_by_seed.setdefault(seed_qn, []).append({
                "qualified_name": row["qualified_name"],
                "provenance": row.get("provenance"),
            })
    if "callees" in include_n:
        for row in _expansion_rows(
            backend, _CALLEES_CYPHER, qns, per_seed_limit,
        ):
            seed_qn = row.get("seed_qn")
            if seed_qn is None or row.get("qualified_name") is None:
                continue
            callees_by_seed.setdefault(seed_qn, []).append({
                "qualified_name": row["qualified_name"],
                "provenance": row.get("provenance"),
            })
    if "tests" in include_n:
        for row in _expansion_rows(
            backend, _TESTS_CYPHER, qns, per_seed_limit,
        ):
            seed_qn = row.get("seed_qn")
            if seed_qn is None or row.get("qualified_name") is None:
                continue
            tests_by_seed.setdefault(seed_qn, []).append({
                "qualified_name": row["qualified_name"],
            })

    results: list[dict[str, Any]] = []
    for s in seeds:
        qn = s.get("qualified_name")
        out: dict[str, Any] = {
            "qualified_name": qn,
            "kind": s.get("kind"),
            "score": float(s.get("score") or 0.0),
            "file": s.get("file"),
            "line": s.get("start_line"),
        }
        if "callers" in include_n:
            out["callers"] = callers_by_seed.get(qn, [])
        if "callees" in include_n:
            out["callees"] = callees_by_seed.get(qn, [])
        if "tests" in include_n:
            out["tests"] = tests_by_seed.get(qn, [])
        results.append(out)

    return {
        "results": results,
        "model": provider.name,
        "embedded_count": _embedded_count(backend, project),
        "warning": None,
    }
