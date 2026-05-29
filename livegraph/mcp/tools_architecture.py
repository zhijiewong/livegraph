"""MCP tools that analyze project architecture (Phase 11).

Three tools: ``find_cycles``, ``layering_violations``, ``hubs``.
All read-only; no new node or edge types are introduced. This file
will grow as Tasks 3 and 4 append the other two tools.
"""
from __future__ import annotations

from typing import Any

from livegraph.graph.backend import GraphBackend
from livegraph.mcp._tarjan import strongly_connected_components

_VALID_KINDS = ("any", "function", "method")
_VALID_PROVENANCE = ("any", "static", "runtime")
_VALID_SCOPES = ("call", "module")
_VALID_EDGE_KINDS = ("any", "imports", "calls")

_MAX_LIMIT = 100
_MAX_VIOLATIONS = 200
_MAX_MIN_FANIN = 1000
_MAX_MIN_SIZE = 100


# ---- find_cycles ----------------------------------------------------

_CALL_EDGES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(src) "
    "WHERE src:Function OR src:Method "
    "MATCH (src)-[c:CALLS]->(tgt) "
    "WHERE ($provenance = 'any' "
    "    OR ($provenance = 'static' AND c.static = true) "
    "    OR ($provenance = 'runtime' AND c.runtime = true)) "
    "RETURN src.qualified_name AS source, "
    "       tgt.qualified_name AS target"
)

_MODULE_EDGES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
    "-[:IMPORTS]->(tgt:File) "
    "RETURN src.path AS source, tgt.path AS target"
)


def find_cycles(
    backend: GraphBackend,
    project: str,
    scope: str = "call",
    provenance: str = "any",
    min_size: int = 2,
    limit: int = 20,
) -> dict[str, Any]:
    """Return strongly-connected components in the call or import graph."""
    if scope not in _VALID_SCOPES:
        return {
            "scope": scope, "provenance": provenance,
            "cycles": [],
            "warning": (
                f"invalid scope {scope!r}; "
                f"must be one of {list(_VALID_SCOPES)}"
            ),
        }
    if scope == "call" and provenance not in _VALID_PROVENANCE:
        return {
            "scope": scope, "provenance": provenance,
            "cycles": [],
            "warning": (
                f"invalid provenance {provenance!r}; "
                f"must be one of {list(_VALID_PROVENANCE)}"
            ),
        }
    min_size = max(1, min(int(min_size), _MAX_MIN_SIZE))
    limit = max(1, min(int(limit), _MAX_LIMIT))

    if scope == "module":
        rows = backend.execute(_MODULE_EDGES_CYPHER, project=project)
    else:
        rows = backend.execute(
            _CALL_EDGES_CYPHER, project=project, provenance=provenance,
        )

    graph: dict[str, list[str]] = {}
    for row in rows:
        s = row.get("source")
        t = row.get("target")
        if s is None or t is None:
            continue
        graph.setdefault(s, []).append(t)
        graph.setdefault(t, [])

    sccs = strongly_connected_components(graph)

    cycles: list[dict[str, Any]] = []
    for component in sccs:
        if len(component) < min_size:
            continue
        # Singleton with no self-loop is acyclic; filter unless min_size=1
        # AND there's a self-loop.
        if len(component) == 1:
            node = component[0]
            if node not in graph.get(node, []):
                continue
        cycles.append({
            "size": len(component),
            "nodes": sorted(component),
        })

    cycles.sort(key=lambda c: (-c["size"], c["nodes"][0]))
    cycles = cycles[:limit]

    warning = None
    if not cycles and not graph:
        warning = (
            "no project data; run `livegraph build` to ingest the project"
        )

    return {
        "scope": scope, "provenance": provenance,
        "cycles": cycles, "warning": warning,
    }
