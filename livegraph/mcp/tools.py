"""Pure-function MCP tool implementations.

Each tool function takes the GraphBackend and project name explicitly so
it is trivially unit-testable with FakeBackend. The MCP server in
``server.py`` is the only place that holds backend/project state and
wraps these functions for FastMCP registration.
"""
from __future__ import annotations

from typing import Any

from livegraph.graph.backend import GraphBackend

# Labels we treat as a primary "kind" for SymbolRef.
_KIND_LABELS = ("Function", "Method", "Class")


def _kind_from_labels(labels: list[str] | None) -> str | None:
    """Return the first known kind label found in ``labels``, lowercased."""
    if not labels:
        return None
    for label in labels:
        if label in _KIND_LABELS:
            return label.lower()
    return None


def _symbol_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a Cypher row into the canonical SymbolRef shape.

    The Cypher query is responsible for returning these exact keys.
    """
    return {
        "qualified_name": row["qualified_name"],
        "name": row["name"],
        "kind": row["kind"],
        "file": row["file"],
        "start_line": row["start_line"],
        "end_line": row["end_line"],
    }
