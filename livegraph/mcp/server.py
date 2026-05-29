"""FastMCP server that exposes livegraph's 15 read-only tools over stdio.

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
from livegraph.mcp.tools_neighborhood import (
    semantic_neighborhood as _semantic_neighborhood,
)
from livegraph.semantic.provider import (
    EmbeddingExtraMissing, EmbeddingProvider,
)

# Set by ``bootstrap()`` before any tool is invoked.
_BACKEND: GraphBackend | None = None
_PROJECT: str | None = None
_PROVIDER: EmbeddingProvider | None = None


def _get_or_load_provider() -> EmbeddingProvider | None:
    """Return the lazily-loaded LocalSTProvider, or None on load failure.

    Catches both `EmbeddingExtraMissing` (extra not installed) and any
    other exception during model construction (network failure, bad model
    name, disk full, etc.). Logs the failure to stderr so operators can
    debug; the MCP tool surfaces a graceful warning either way.
    """
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER
    try:
        from livegraph.config import load_settings
        from livegraph.semantic.provider import LocalSTProvider

        settings = load_settings()
        _PROVIDER = LocalSTProvider(
            model_name=settings.livegraph_embed_model,
            batch_size=settings.livegraph_embed_batch_size,
        )
    except EmbeddingExtraMissing:
        return None
    except Exception as exc:
        import sys
        print(
            f"livegraph: failed to load embedding model: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None
    return _PROVIDER


def _require_state() -> tuple[GraphBackend, str]:
    if _BACKEND is None or _PROJECT is None:
        raise RuntimeError(
            "livegraph MCP server not bootstrapped — "
            "call bootstrap(backend, project) first."
        )
    return _BACKEND, _PROJECT


def build_server(default_row_limit: int = 1000,
                 default_timeout_seconds: int = 30) -> FastMCP:
    """Construct a FastMCP server with all 15 tools registered.

    Tool wrappers reference the module-level state set by ``bootstrap``.
    ``default_row_limit`` and ``default_timeout_seconds`` are the values
    used when ``run_cypher`` is called without explicit overrides.
    """
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

    @mcp.tool()
    def describe_schema() -> dict[str, Any]:
        """Return the static schema description for the configured project.

        Includes node labels, edge types, safety rules, the auto-injected
        $project parameter convention, and six example queries showing the
        idioms (project scoping, label routing, provenance flags).

        The agent should call this once per session and cache the response.
        """
        backend, project = _require_state()
        return tools.describe_schema(backend, project)

    @mcp.tool()
    def run_cypher(
        query: str, params: dict[str, Any] | None = None,
        row_limit: int = default_row_limit,
        timeout_seconds: int = default_timeout_seconds,
    ) -> dict[str, Any]:
        """Run a read-only Cypher query against the project's graph.

        - ``query``: Cypher string. The forbidden-keyword scan (CREATE,
          MERGE, DELETE, SET, REMOVE, DROP, LOAD CSV, USING PERIODIC
          COMMIT, CALL) is applied to ``query`` before execution.
          ``$project`` is auto-injected unless ``params`` overrides it.
        - ``params``: parameter map for the query.
        - ``row_limit``: server-side truncation. If exceeded the response
          includes ``truncated: true``.
        - ``timeout_seconds``: per-transaction timeout.

        Returns ``{rows, truncated, row_count, summary}``.
        """
        backend, project = _require_state()
        return tools.run_cypher(
            backend, project, query=query, params=params,
            row_limit=row_limit, timeout_seconds=timeout_seconds,
        )

    @mcp.tool()
    def semantic_search(
        query: str, limit: int = 10, kind: str = "any",
    ) -> dict[str, Any]:
        """Find code symbols by vector similarity to a natural-language query.

        - ``query``: natural-language description of the code you want to find.
        - ``limit``: top-K results (default 10).
        - ``kind``: ``"any"`` (default), ``"function"``, or ``"method"``.

        Returns ``{results, model, embedded_count, warning}``. If the
        ``[semantic]`` extra is not installed, returns an empty result list
        and a warning with the install hint.
        """
        backend, project = _require_state()
        provider = _get_or_load_provider()
        if provider is None:
            return {
                "results": [],
                "model": "unknown",
                "embedded_count": 0,
                "warning": (
                    "semantic search not enabled — install with "
                    "`pip install livegraph[semantic]`"
                ),
            }
        return tools.semantic_search(
            backend, project, provider, query=query,
            limit=limit, kind=kind,
        )

    @mcp.tool()
    def semantic_neighborhood(
        query: str,
        limit: int = 10,
        per_seed_limit: int = 10,
        kind: str = "any",
        include: list[str] | None = None,
        min_score: float = 0.0,
    ) -> dict[str, Any]:
        """Vector seeds + per-seed callers/callees/tests in one call.

        For each top-K semantic match to ``query``, returns the direct
        callers (with ``static``/``runtime``/``both`` provenance),
        callees (same), and tests that cover the symbol. Use this when
        you want "where do I look, what do I run" rather than just
        "what matches."

        - ``limit``: top-K seeds (max 50).
        - ``per_seed_limit``: cap per expansion list (max 50).
        - ``kind``: ``"any"`` (default), ``"function"``, or ``"method"``.
        - ``include``: subset of ``{"callers","callees","tests"}``.
          Default is all three.
        - ``min_score``: drop seeds below this cosine score.

        Returns ``{results, model, embedded_count, warning}``. Graceful
        degradation when the ``[semantic]`` extra isn't installed.
        """
        backend, project = _require_state()
        provider = _get_or_load_provider()
        if provider is None:
            return {
                "results": [],
                "model": "unknown",
                "embedded_count": 0,
                "warning": (
                    "semantic search not enabled — install with "
                    "`pip install livegraph[semantic]`"
                ),
            }
        return _semantic_neighborhood(
            backend, project, provider, query=query, limit=limit,
            per_seed_limit=per_seed_limit, kind=kind,
            include=include, min_score=min_score,
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


def bootstrap(
    backend: GraphBackend, project: str,
    default_row_limit: int = 1000,
    default_timeout_seconds: int = 30,
) -> FastMCP:
    """Initialize global state and return a configured FastMCP server."""
    global _BACKEND, _PROJECT, _PROVIDER
    _BACKEND = backend
    _PROJECT = project
    _PROVIDER = None
    _warn_if_project_missing(backend, project)
    return build_server(
        default_row_limit=default_row_limit,
        default_timeout_seconds=default_timeout_seconds,
    )


def run_stdio(
    backend: GraphBackend, project: str,
    default_row_limit: int = 1000,
    default_timeout_seconds: int = 30,
) -> None:
    """Launch the server and serve stdio until stdin closes."""
    server = bootstrap(
        backend, project,
        default_row_limit=default_row_limit,
        default_timeout_seconds=default_timeout_seconds,
    )
    server.run()  # FastMCP defaults to stdio transport
