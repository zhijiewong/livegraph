"""FastMCP server that exposes livegraph's 11 read-only tools over stdio.

The module-level ``_BACKEND`` and ``_PROJECT`` globals are set once via
``bootstrap()`` at startup. Each FastMCP-registered wrapper calls into
``livegraph.mcp.tools`` with those globals — keeping tool implementations
pure and unit-testable while still presenting a clean MCP surface.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from livegraph.graph.backend import GraphBackend
from livegraph.mcp import tools

# Set by ``bootstrap()`` before any tool is invoked.
_BACKEND: GraphBackend | None = None
_PROJECT: str | None = None


def _require_state() -> tuple[GraphBackend, str]:
    if _BACKEND is None or _PROJECT is None:
        raise RuntimeError(
            "livegraph MCP server not bootstrapped — "
            "call bootstrap(backend, project) first."
        )
    return _BACKEND, _PROJECT


def build_server() -> FastMCP:
    """Construct a FastMCP server with all 11 tools registered."""
    mcp = FastMCP("livegraph")

    @mcp.tool()
    def find_symbol(query: str, exact: bool = False,
                    limit: int = 50) -> list[dict[str, Any]]:
        """Find project symbols by name (substring or exact)."""
        backend, project = _require_state()
        return tools.find_symbol(backend, project, query=query,
                                 exact=exact, limit=limit)

    @mcp.tool()
    def get_source(qualified_name: str) -> dict[str, Any] | None:
        """Return a symbol's source + coverage stats, or null."""
        backend, project = _require_state()
        return tools.get_source(backend, project,
                                qualified_name=qualified_name)

    @mcp.tool()
    def find_callers(qualified_name: str, provenance: str = "any",
                     limit: int = 50) -> list[dict[str, Any]]:
        """Who calls this symbol (filterable by static/runtime/any)."""
        backend, project = _require_state()
        return tools.find_callers(backend, project,
                                  qualified_name=qualified_name,
                                  provenance=provenance, limit=limit)

    @mcp.tool()
    def find_callees(qualified_name: str, provenance: str = "any",
                     limit: int = 50) -> list[dict[str, Any]]:
        """What this symbol calls (filterable by static/runtime/any)."""
        backend, project = _require_state()
        return tools.find_callees(backend, project,
                                  qualified_name=qualified_name,
                                  provenance=provenance, limit=limit)

    @mcp.tool()
    def runtime_only_calls(file: str | None = None,
                           limit: int = 100) -> list[dict[str, Any]]:
        """Calls runtime observed but static analysis missed."""
        backend, project = _require_state()
        return tools.runtime_only_calls(backend, project,
                                        file=file, limit=limit)

    @mcp.tool()
    def dead_static_calls(file: str | None = None,
                          limit: int = 100) -> list[dict[str, Any]]:
        """Calls static analysis predicted but no test exercised."""
        backend, project = _require_state()
        return tools.dead_static_calls(backend, project,
                                       file=file, limit=limit)

    @mcp.tool()
    def tests_for(qualified_name: str,
                  limit: int = 50) -> list[dict[str, Any]]:
        """Tests that cover this symbol, with per-test coverage."""
        backend, project = _require_state()
        return tools.tests_for(backend, project,
                               qualified_name=qualified_name, limit=limit)

    @mcp.tool()
    def untested_symbols(file: str | None = None, kind: str = "any",
                         limit: int = 100) -> list[dict[str, Any]]:
        """Functions/methods the test suite never exercised."""
        backend, project = _require_state()
        return tools.untested_symbols(backend, project, file=file,
                                      kind=kind, limit=limit)

    @mcp.tool()
    def imports(file: str, direction: str = "out",
                limit: int = 100) -> list[dict[str, Any]]:
        """Outgoing (out) or incoming (in) imports for a file."""
        backend, project = _require_state()
        return tools.imports(backend, project, file=file,
                             direction=direction, limit=limit)

    @mcp.tool()
    def graph_status() -> dict[str, Any]:
        """Counts: files, symbols, tests, calls split by provenance."""
        backend, project = _require_state()
        return tools.graph_status(backend, project)

    @mcp.tool()
    def change_impact(
        diff: str, max_depth: int = 5, provenance: str = "any",
        limit: int = 200,
    ) -> dict[str, Any]:
        """Given a unified diff, return changed/impacted symbols and tests to run.

        - ``diff``: unified-diff text (e.g. ``git diff HEAD~1 HEAD``).
        - ``max_depth``: how far to traverse CALLS upstream (clamped 1..20).
        - ``provenance``: edge filter — ``any``, ``static``, or ``runtime``.
        - ``limit``: max number of impacted symbols returned.

        Returns ``{changed, impacted, tests_to_run, unmatched_files, stats}``.
        """
        backend, project = _require_state()
        return tools.change_impact(
            backend, project, diff=diff,
            max_depth=max_depth, provenance=provenance, limit=limit,
        )

    return mcp


def _warn_if_project_missing(backend: GraphBackend, project: str) -> None:
    """Log a stderr warning if the project node doesn't exist."""
    import sys
    try:
        rows = backend.execute(
            "MATCH (p:Project {name: $project}) RETURN count(p) AS n",
            project=project,
        )
        if rows and (rows[0].get("n") or 0) == 0:
            print(
                f"warning: project {project!r} not found in graph — "
                f"tool calls will return empty results until you run "
                f"`livegraph build` for this project.",
                file=sys.stderr,
            )
    except Exception:
        # Don't fail bootstrap on a probe error; tool calls will surface real issues.
        pass


def bootstrap(backend: GraphBackend, project: str) -> FastMCP:
    """Initialize global state and return a configured FastMCP server."""
    global _BACKEND, _PROJECT
    _BACKEND = backend
    _PROJECT = project
    _warn_if_project_missing(backend, project)
    return build_server()


def run_stdio(backend: GraphBackend, project: str) -> None:
    """Launch the server and serve stdio until stdin closes."""
    server = bootstrap(backend, project)
    server.run()  # FastMCP defaults to stdio transport
