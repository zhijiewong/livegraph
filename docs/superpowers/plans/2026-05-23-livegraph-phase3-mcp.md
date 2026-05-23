# livegraph Phase 3 — MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 3 MCP server: a `livegraph mcp` subcommand that runs a stdio MCP server exposing 10 curated read-only tools over the Phase 1+2 Neo4j graph.

**Architecture:** Pure-function tools in `livegraph/mcp/tools.py` take a `GraphBackend` plus a project name plus typed inputs and return dicts; a thin FastMCP shim in `livegraph/mcp/server.py` registers them with the MCP SDK and runs the stdio loop. Every Cypher query traverses from the configured `Project` node so results stay scoped. A new `livegraph mcp [--project NAME]` subcommand boots the server with config from `Settings`.

**Tech Stack:** Python 3.12+, `mcp>=1.10` (official Anthropic SDK, FastMCP API), reuses the existing `neo4j` driver, `Neo4jBackend`, `Settings`, and `typer` CLI. No new graph or model dependencies.

**Reference:** Design spec at `docs/superpowers/specs/2026-05-23-livegraph-phase3-mcp-design.md`.

**Conventions for every task:**
- Run tests from the repo root: `cd /Users/yvon.zhu/Documents/GitHub/livegraph`.
- Unit tests need no Neo4j. Integration tests are `@pytest.mark.integration` and need Neo4j up (`brew services start neo4j` or `docker compose up -d`).
- Commit after each task with the shown message. If git complains about author identity, use `git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit ...`.
- All work happens on a feature branch (`implement-phase-3-mcp`) created in Task 1.

---

## Task 1: Branch + package scaffolding

**Files:**
- Create: `livegraph/mcp/__init__.py` (empty)
- Modify: `pyproject.toml`
- Modify: `.env.example`

- [ ] **Step 1: Create the feature branch**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
git checkout main
git pull --ff-only
git checkout -b implement-phase-3-mcp
```

- [ ] **Step 2: Add `mcp>=1.10` to `pyproject.toml`**

Edit `pyproject.toml`. In the `dependencies` list, append `"mcp>=1.10"`. The full block should look like:

```toml
dependencies = [
    "tree-sitter>=0.23",
    "tree-sitter-python>=0.23",
    "neo4j>=5.26",
    "neo4j-rust-ext",
    "coverage>=7.6",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "typer>=0.15",
    "mcp>=1.10",
]
```

- [ ] **Step 3: Install the new dep**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
.venv/bin/pip install -e ".[dev]" 2>&1 | tail -5
```
Expected: `Successfully installed mcp-1.x.x ...` (plus its deps). If `mcp` fails to resolve, try `.venv/bin/pip install "mcp>=1.0"` first and report the resolved version.

- [ ] **Step 4: Create `livegraph/mcp/__init__.py`**

```bash
touch livegraph/mcp/__init__.py
```

The file is zero bytes.

- [ ] **Step 5: Append to `.env.example`**

Add this line at the end of `.env.example`:

```
# Phase 3 (MCP server) — name of the ingested project this server serves.
LIVEGRAPH_PROJECT=
```

- [ ] **Step 6: Verify nothing regressed**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: all unit tests pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
git add -A
git commit -m "chore: scaffold mcp package, add mcp dep, env example"
```

---

## Task 2: Add `livegraph_project` to Settings

**Files:**
- Modify: `livegraph/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Append failing test to `tests/unit/test_config.py`**

```python
def test_livegraph_project_defaults_to_none(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.livegraph_project is None


def test_livegraph_project_from_env(monkeypatch):
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "myproject")
    settings = Settings(_env_file=None)
    assert settings.livegraph_project == "myproject"
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_config.py::test_livegraph_project_defaults_to_none -v
```
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'livegraph_project'`.

- [ ] **Step 3: Add the field to `livegraph/config.py`**

In `livegraph/config.py`, inside the `Settings` class, after the existing `livegraph_log_level` line, add:

```python
    livegraph_project: str | None = None
```

The full block ends up as:

```python
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "livegraph-local"
    livegraph_batch_size: int = 1000
    livegraph_log_level: str = "INFO"
    livegraph_project: str | None = None
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_config.py -v 2>&1 | tail -8
```
Expected: 4 passed (2 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add livegraph/config.py tests/unit/test_config.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: add livegraph_project setting for MCP server"
```

---

## Task 3: Tool infrastructure — symbol shape helper

**Files:**
- Create: `livegraph/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools_helpers.py`

This task adds the shared helpers every tool needs: a `_symbol_from_row` mapper that turns a Cypher record into the canonical `SymbolRef` dict. Every later tool task uses it.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mcp_tools_helpers.py
from livegraph.mcp.tools import _symbol_from_row, _kind_from_labels


def test_kind_from_labels_picks_first_known_label():
    assert _kind_from_labels(["Function"]) == "function"
    assert _kind_from_labels(["Function", "Test"]) == "function"
    assert _kind_from_labels(["Method"]) == "method"
    assert _kind_from_labels(["Class"]) == "class"


def test_kind_from_labels_unknown_returns_none():
    assert _kind_from_labels(["UnknownLabel"]) is None
    assert _kind_from_labels([]) is None


def test_symbol_from_row_maps_canonical_fields():
    row = {
        "qualified_name": "a.py::f", "name": "f", "kind": "function",
        "file": "a.py", "start_line": 1, "end_line": 3,
    }
    assert _symbol_from_row(row) == {
        "qualified_name": "a.py::f", "name": "f", "kind": "function",
        "file": "a.py", "start_line": 1, "end_line": 3,
    }
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_helpers.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.mcp.tools'`.

- [ ] **Step 3: Write `livegraph/mcp/tools.py`**

```python
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
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_helpers.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools.py tests/unit/test_mcp_tools_helpers.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: SymbolRef helpers for MCP tools"
```

---

## Task 4: Tools — `find_symbol` and `get_source`

**Files:**
- Modify: `livegraph/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools_navigation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mcp_tools_navigation.py
from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import find_symbol, get_source


def test_find_symbol_substring_returns_symbols():
    rows = [
        {"qualified_name": "a.py::run_operation", "name": "run_operation",
         "kind": "function", "file": "a.py",
         "start_line": 1, "end_line": 5},
        {"qualified_name": "b.py::run", "name": "run", "kind": "method",
         "file": "b.py", "start_line": 10, "end_line": 20},
    ]
    backend = FakeBackend(rows=rows)
    results = find_symbol(backend, project="sample", query="run")
    assert len(results) == 2
    assert results[0]["qualified_name"] == "a.py::run_operation"
    # Verify project is in the parameters of the issued query.
    _q, params = backend.calls[0]
    assert params["project"] == "sample"
    assert params["query"] == "run"
    assert params["exact"] is False
    assert params["limit"] == 50


def test_find_symbol_exact_passes_flag():
    backend = FakeBackend(rows=[])
    find_symbol(backend, project="p", query="run", exact=True, limit=10)
    _q, params = backend.calls[0]
    assert params["exact"] is True
    assert params["limit"] == 10


def test_get_source_returns_full_symbol_with_metadata():
    row = {
        "qualified_name": "a.py::f", "name": "f", "kind": "function",
        "file": "a.py", "start_line": 1, "end_line": 3,
        "decorators": ["staticmethod"], "source": "def f(): pass",
        "runtime_observed": True, "coverage_pct": 80.0,
    }
    backend = FakeBackend(rows=[row])
    result = get_source(backend, project="p", qualified_name="a.py::f")
    assert result is not None
    assert result["qualified_name"] == "a.py::f"
    assert result["decorators"] == ["staticmethod"]
    assert result["runtime_observed"] is True
    assert result["coverage_pct"] == 80.0


def test_get_source_returns_none_when_missing():
    backend = FakeBackend(rows=[])
    assert get_source(backend, project="p", qualified_name="missing") is None
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_navigation.py -v
```
Expected: FAIL — `ImportError: cannot import name 'find_symbol' from 'livegraph.mcp.tools'`.

- [ ] **Step 3: Append the two tools to `livegraph/mcp/tools.py`**

Add these functions and Cypher constants at the end of `livegraph/mcp/tools.py`:

```python
# -- find_symbol -------------------------------------------------------

_FIND_SYMBOL_CYPHER = (
    "MATCH (p:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE (s:Function OR s:Method OR s:Class) AND ("
    "  ($exact AND s.name = $query) "
    "  OR (NOT $exact AND toLower(s.name) CONTAINS toLower($query)) "
    ") "
    "RETURN s.qualified_name AS qualified_name, s.name AS name, "
    "       head([l IN labels(s) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       s.file AS file, s.start_line AS start_line, "
    "       s.end_line AS end_line "
    "ORDER BY s.qualified_name "
    "LIMIT $limit"
)


def find_symbol(backend: GraphBackend, project: str, query: str,
                exact: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    """Find symbols by name. Substring (case-insensitive) unless ``exact``."""
    rows = backend.execute(
        _FIND_SYMBOL_CYPHER,
        project=project, query=query, exact=exact, limit=limit,
    )
    return [_symbol_from_row(r) for r in rows]


# -- get_source --------------------------------------------------------

_GET_SOURCE_CYPHER = (
    "MATCH (p:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE s.qualified_name = $qualified_name "
    "RETURN s.qualified_name AS qualified_name, s.name AS name, "
    "       head([l IN labels(s) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       s.file AS file, s.start_line AS start_line, "
    "       s.end_line AS end_line, "
    "       coalesce(s.decorators, []) AS decorators, "
    "       coalesce(s.source, '') AS source, "
    "       coalesce(s.runtime_observed, false) AS runtime_observed, "
    "       coalesce(s.coverage_pct, 0.0) AS coverage_pct "
    "LIMIT 1"
)


def get_source(backend: GraphBackend, project: str,
               qualified_name: str) -> dict[str, Any] | None:
    """Return the full source + metadata for a symbol, or None."""
    rows = backend.execute(
        _GET_SOURCE_CYPHER, project=project, qualified_name=qualified_name,
    )
    if not rows:
        return None
    row = rows[0]
    return {
        **_symbol_from_row(row),
        "decorators": list(row.get("decorators") or []),
        "source": row.get("source") or "",
        "runtime_observed": bool(row.get("runtime_observed")),
        "coverage_pct": float(row.get("coverage_pct") or 0.0),
    }
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_navigation.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools.py tests/unit/test_mcp_tools_navigation.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: MCP tools find_symbol and get_source"
```

---

## Task 5: Tools — `find_callers` and `find_callees`

**Files:**
- Modify: `livegraph/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools_calls.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mcp_tools_calls.py
from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import find_callers, find_callees


def test_find_callers_returns_caller_with_edge_provenance():
    row = {
        "qualified_name": "a.py::g", "name": "g", "kind": "function",
        "file": "a.py", "start_line": 1, "end_line": 2,
        "static": True, "runtime": False, "observed_count": 0,
        "call_site_lines": [],
    }
    backend = FakeBackend(rows=[row])
    results = find_callers(backend, project="p", qualified_name="a.py::f")
    assert len(results) == 1
    assert results[0]["caller"]["qualified_name"] == "a.py::g"
    assert results[0]["edge"]["static"] is True
    assert results[0]["edge"]["runtime"] is False
    _q, params = backend.calls[0]
    assert params["provenance"] == "any"


def test_find_callers_passes_provenance_filter():
    backend = FakeBackend(rows=[])
    find_callers(backend, project="p", qualified_name="x",
                 provenance="runtime", limit=5)
    _q, params = backend.calls[0]
    assert params["provenance"] == "runtime"
    assert params["limit"] == 5


def test_find_callees_returns_callee_with_edge_provenance():
    row = {
        "qualified_name": "a.py::h", "name": "h", "kind": "method",
        "file": "a.py", "start_line": 5, "end_line": 6,
        "static": False, "runtime": True, "observed_count": 3,
        "call_site_lines": [12, 17],
    }
    backend = FakeBackend(rows=[row])
    results = find_callees(backend, project="p", qualified_name="a.py::f")
    assert results[0]["callee"]["qualified_name"] == "a.py::h"
    assert results[0]["edge"]["observed_count"] == 3
    assert results[0]["edge"]["call_site_lines"] == [12, 17]
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_calls.py -v
```
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Append to `livegraph/mcp/tools.py`**

```python
# -- find_callers / find_callees --------------------------------------

_PROVENANCE_PREDICATE = (
    "($provenance = 'any' "
    " OR ($provenance = 'static' AND c.static = true) "
    " OR ($provenance = 'runtime' AND c.runtime = true))"
)

_FIND_CALLERS_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(callee) "
    "WHERE callee.qualified_name = $qualified_name "
    "MATCH (caller)-[c:CALLS]->(callee) "
    f"WHERE {_PROVENANCE_PREDICATE} "
    "RETURN caller.qualified_name AS qualified_name, "
    "       caller.name AS name, "
    "       head([l IN labels(caller) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       caller.file AS file, caller.start_line AS start_line, "
    "       caller.end_line AS end_line, "
    "       c.static AS static, coalesce(c.runtime, false) AS runtime, "
    "       coalesce(c.observed_count, 0) AS observed_count, "
    "       coalesce(c.call_site_lines, []) AS call_site_lines "
    "LIMIT $limit"
)

_FIND_CALLEES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(caller) "
    "WHERE caller.qualified_name = $qualified_name "
    "MATCH (caller)-[c:CALLS]->(callee) "
    f"WHERE {_PROVENANCE_PREDICATE} "
    "RETURN callee.qualified_name AS qualified_name, "
    "       callee.name AS name, "
    "       head([l IN labels(callee) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       callee.file AS file, callee.start_line AS start_line, "
    "       callee.end_line AS end_line, "
    "       c.static AS static, coalesce(c.runtime, false) AS runtime, "
    "       coalesce(c.observed_count, 0) AS observed_count, "
    "       coalesce(c.call_site_lines, []) AS call_site_lines "
    "LIMIT $limit"
)


def _edge_provenance(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "static": bool(row.get("static")),
        "runtime": bool(row.get("runtime")),
        "observed_count": int(row.get("observed_count") or 0),
        "call_site_lines": list(row.get("call_site_lines") or []),
    }


def find_callers(backend: GraphBackend, project: str, qualified_name: str,
                 provenance: str = "any",
                 limit: int = 50) -> list[dict[str, Any]]:
    """Return who calls ``qualified_name``, filtered by ``provenance``."""
    rows = backend.execute(
        _FIND_CALLERS_CYPHER, project=project,
        qualified_name=qualified_name, provenance=provenance, limit=limit,
    )
    return [
        {"caller": _symbol_from_row(r), "edge": _edge_provenance(r)}
        for r in rows
    ]


def find_callees(backend: GraphBackend, project: str, qualified_name: str,
                 provenance: str = "any",
                 limit: int = 50) -> list[dict[str, Any]]:
    """Return what ``qualified_name`` calls, filtered by ``provenance``."""
    rows = backend.execute(
        _FIND_CALLEES_CYPHER, project=project,
        qualified_name=qualified_name, provenance=provenance, limit=limit,
    )
    return [
        {"callee": _symbol_from_row(r), "edge": _edge_provenance(r)}
        for r in rows
    ]
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_calls.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools.py tests/unit/test_mcp_tools_calls.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: MCP tools find_callers and find_callees"
```

---

## Task 6: Tools — `runtime_only_calls` and `dead_static_calls` (the differentiators)

**Files:**
- Modify: `livegraph/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools_differentiators.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mcp_tools_differentiators.py
from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import runtime_only_calls, dead_static_calls


def test_runtime_only_calls_emits_pairs_with_count():
    row = {
        "caller_qualified_name": "runner.py::run_operation",
        "caller_name": "run_operation", "caller_kind": "function",
        "caller_file": "runner.py", "caller_start_line": 7,
        "caller_end_line": 8,
        "callee_qualified_name": "calculator.py::Calculator.add",
        "callee_name": "add", "callee_kind": "method",
        "callee_file": "calculator.py", "callee_start_line": 6,
        "callee_end_line": 7,
        "observed_count": 4,
    }
    backend = FakeBackend(rows=[row])
    results = runtime_only_calls(backend, project="sample")
    assert len(results) == 1
    assert results[0]["caller"]["qualified_name"] == "runner.py::run_operation"
    assert results[0]["callee"]["qualified_name"] == "calculator.py::Calculator.add"
    assert results[0]["observed_count"] == 4
    _q, params = backend.calls[0]
    assert params["file"] is None
    assert params["limit"] == 100


def test_runtime_only_calls_passes_file_filter():
    backend = FakeBackend(rows=[])
    runtime_only_calls(backend, project="p", file="runner.py", limit=10)
    _q, params = backend.calls[0]
    assert params["file"] == "runner.py"
    assert params["limit"] == 10


def test_dead_static_calls_returns_caller_callee_pairs():
    row = {
        "caller_qualified_name": "a.py::main", "caller_name": "main",
        "caller_kind": "function", "caller_file": "a.py",
        "caller_start_line": 1, "caller_end_line": 5,
        "callee_qualified_name": "a.py::unused", "callee_name": "unused",
        "callee_kind": "function", "callee_file": "a.py",
        "callee_start_line": 10, "callee_end_line": 12,
    }
    backend = FakeBackend(rows=[row])
    results = dead_static_calls(backend, project="p")
    assert results[0]["caller"]["qualified_name"] == "a.py::main"
    assert results[0]["callee"]["qualified_name"] == "a.py::unused"
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_differentiators.py -v
```
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Append to `livegraph/mcp/tools.py`**

```python
# -- runtime_only_calls / dead_static_calls ---------------------------

_CALL_PAIR_RETURN = (
    "RETURN caller.qualified_name AS caller_qualified_name, "
    "       caller.name AS caller_name, "
    "       head([l IN labels(caller) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS caller_kind, "
    "       caller.file AS caller_file, "
    "       caller.start_line AS caller_start_line, "
    "       caller.end_line AS caller_end_line, "
    "       callee.qualified_name AS callee_qualified_name, "
    "       callee.name AS callee_name, "
    "       head([l IN labels(callee) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS callee_kind, "
    "       callee.file AS callee_file, "
    "       callee.start_line AS callee_start_line, "
    "       callee.end_line AS callee_end_line, "
    "       coalesce(c.observed_count, 0) AS observed_count "
)

_RUNTIME_ONLY_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(file:File) "
    "WHERE $file IS NULL OR file.path = $file "
    "MATCH (file)-[:DEFINES|HAS_METHOD*1..2]->(caller)-[c:CALLS]->(callee) "
    "WHERE c.runtime = true AND coalesce(c.static, false) = false "
    + _CALL_PAIR_RETURN +
    "LIMIT $limit"
)

_DEAD_STATIC_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(file:File) "
    "WHERE $file IS NULL OR file.path = $file "
    "MATCH (file)-[:DEFINES|HAS_METHOD*1..2]->(caller)-[c:CALLS]->(callee) "
    "WHERE c.static = true AND coalesce(c.runtime, false) = false "
    + _CALL_PAIR_RETURN +
    "LIMIT $limit"
)


def _pair_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "caller": _symbol_from_row({
            "qualified_name": row["caller_qualified_name"],
            "name": row["caller_name"], "kind": row["caller_kind"],
            "file": row["caller_file"],
            "start_line": row["caller_start_line"],
            "end_line": row["caller_end_line"],
        }),
        "callee": _symbol_from_row({
            "qualified_name": row["callee_qualified_name"],
            "name": row["callee_name"], "kind": row["callee_kind"],
            "file": row["callee_file"],
            "start_line": row["callee_start_line"],
            "end_line": row["callee_end_line"],
        }),
    }


def runtime_only_calls(
    backend: GraphBackend, project: str, file: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Calls observed at runtime that static analysis did NOT predict.

    This is the headline livegraph query — the dynamic-dispatch edges
    no purely static code-graph tool can produce.
    """
    rows = backend.execute(
        _RUNTIME_ONLY_CYPHER, project=project, file=file, limit=limit,
    )
    return [
        {**_pair_from_row(r),
         "observed_count": int(r.get("observed_count") or 0)}
        for r in rows
    ]


def dead_static_calls(
    backend: GraphBackend, project: str, file: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Calls predicted by static analysis but never observed at runtime.

    These are dead code or untested code — useful as a code-cleanup
    signal and as a coverage gap signal.
    """
    rows = backend.execute(
        _DEAD_STATIC_CYPHER, project=project, file=file, limit=limit,
    )
    return [_pair_from_row(r) for r in rows]
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_differentiators.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools.py tests/unit/test_mcp_tools_differentiators.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: MCP tools runtime_only_calls and dead_static_calls"
```

---

## Task 7: Tools — `tests_for` and `untested_symbols`

**Files:**
- Modify: `livegraph/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools_tests.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mcp_tools_tests.py
from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import tests_for, untested_symbols


def test_tests_for_returns_test_with_coverage():
    row = {
        "qualified_name": "test_a.py::test_x", "name": "test_x",
        "kind": "function", "file": "test_a.py",
        "start_line": 1, "end_line": 3,
        "test_outcome": "passed", "test_duration": 0.02,
        "lines_covered": 3, "lines_total": 4, "coverage_pct": 75.0,
    }
    backend = FakeBackend(rows=[row])
    results = tests_for(backend, project="p",
                       qualified_name="a.py::f")
    assert results[0]["test"]["qualified_name"] == "test_a.py::test_x"
    assert results[0]["test"]["test_outcome"] == "passed"
    assert results[0]["test"]["test_duration"] == 0.02
    assert results[0]["coverage_pct"] == 75.0
    assert results[0]["lines_covered"] == 3
    assert results[0]["lines_total"] == 4


def test_untested_symbols_passes_kind_and_file_filters():
    row = {
        "qualified_name": "a.py::dead", "name": "dead",
        "kind": "function", "file": "a.py",
        "start_line": 1, "end_line": 2,
    }
    backend = FakeBackend(rows=[row])
    results = untested_symbols(backend, project="p", file="a.py",
                               kind="function", limit=10)
    assert results[0]["qualified_name"] == "a.py::dead"
    _q, params = backend.calls[0]
    assert params["file"] == "a.py"
    assert params["kind"] == "function"
    assert params["limit"] == 10


def test_untested_symbols_kind_any_filters_to_function_or_method():
    backend = FakeBackend(rows=[])
    untested_symbols(backend, project="p", kind="any")
    _q, params = backend.calls[0]
    assert params["kind"] == "any"
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_tests.py -v
```
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Append to `livegraph/mcp/tools.py`**

```python
# -- tests_for / untested_symbols -------------------------------------

_TESTS_FOR_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(symbol) "
    "WHERE symbol.qualified_name = $qualified_name "
    "MATCH (t:Test)-[c:COVERS]->(symbol) "
    "RETURN t.qualified_name AS qualified_name, t.name AS name, "
    "       head([l IN labels(t) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       t.file AS file, t.start_line AS start_line, "
    "       t.end_line AS end_line, "
    "       coalesce(t.test_outcome, '') AS test_outcome, "
    "       coalesce(t.test_duration, 0.0) AS test_duration, "
    "       coalesce(c.lines_covered, 0) AS lines_covered, "
    "       coalesce(c.lines_total, 0) AS lines_total, "
    "       coalesce(c.coverage_pct, 0.0) AS coverage_pct "
    "ORDER BY t.qualified_name"
)

_UNTESTED_SYMBOLS_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(file:File) "
    "WHERE $file IS NULL OR file.path = $file "
    "MATCH (file)-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE coalesce(s.runtime_observed, false) = false "
    "  AND ("
    "    ($kind = 'any' AND (s:Function OR s:Method)) "
    "    OR ($kind = 'function' AND s:Function AND NOT s:Test) "
    "    OR ($kind = 'method' AND s:Method) "
    "  ) "
    "RETURN s.qualified_name AS qualified_name, s.name AS name, "
    "       head([l IN labels(s) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       s.file AS file, s.start_line AS start_line, "
    "       s.end_line AS end_line "
    "ORDER BY s.qualified_name "
    "LIMIT $limit"
)


def tests_for(backend: GraphBackend, project: str,
              qualified_name: str) -> list[dict[str, Any]]:
    """Return tests that cover ``qualified_name``, with coverage data."""
    rows = backend.execute(
        _TESTS_FOR_CYPHER, project=project, qualified_name=qualified_name,
    )
    return [
        {
            "test": {
                **_symbol_from_row(r),
                "test_outcome": r.get("test_outcome") or "",
                "test_duration": float(r.get("test_duration") or 0.0),
            },
            "lines_covered": int(r.get("lines_covered") or 0),
            "lines_total": int(r.get("lines_total") or 0),
            "coverage_pct": float(r.get("coverage_pct") or 0.0),
        }
        for r in rows
    ]


def untested_symbols(
    backend: GraphBackend, project: str, file: str | None = None,
    kind: str = "any", limit: int = 100,
) -> list[dict[str, Any]]:
    """Functions/methods that the test suite never exercised."""
    rows = backend.execute(
        _UNTESTED_SYMBOLS_CYPHER, project=project, file=file,
        kind=kind, limit=limit,
    )
    return [_symbol_from_row(r) for r in rows]
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_tests.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools.py tests/unit/test_mcp_tools_tests.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: MCP tools tests_for and untested_symbols"
```

---

## Task 8: Tools — `imports` and `graph_status`

**Files:**
- Modify: `livegraph/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools_structure.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mcp_tools_structure.py
from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import imports, graph_status


def test_imports_out_returns_file_and_module_targets():
    rows = [
        {"target": "pkg/sub.py", "kind": "file",
         "raw": "from pkg.sub import x", "line": 1},
        {"target": "os", "kind": "stdlib", "raw": "import os", "line": 2},
    ]
    backend = FakeBackend(rows=rows)
    results = imports(backend, project="p", file="a.py", direction="out")
    assert results[0]["target"] == "pkg/sub.py"
    assert results[0]["kind"] == "file"
    assert results[1]["kind"] == "stdlib"


def test_imports_in_returns_source_files():
    backend = FakeBackend(rows=[
        {"source_file": "main.py", "raw": "from lib import x", "line": 4},
    ])
    results = imports(backend, project="p", file="lib.py", direction="in")
    assert results[0]["source_file"] == "main.py"
    assert results[0]["raw"] == "from lib import x"


def test_graph_status_summarizes_counts():
    rows = [{
        "project": "sample", "files": 3, "classes": 1,
        "functions": 5, "methods": 2, "tests": 3,
        "calls_total": 7, "calls_runtime_only": 1,
        "calls_static_only": 4, "calls_both": 2,
    }]
    backend = FakeBackend(rows=rows)
    result = graph_status(backend, project="sample")
    assert result["project"] == "sample"
    assert result["files"] == 3
    assert result["calls_runtime_only"] == 1


def test_graph_status_handles_empty_graph():
    backend = FakeBackend(rows=[])
    result = graph_status(backend, project="empty")
    assert result["project"] == "empty"
    assert result["files"] == 0
    assert result["calls_total"] == 0
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_structure.py -v
```
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Append to `livegraph/mcp/tools.py`**

```python
# -- imports / graph_status -------------------------------------------

_IMPORTS_OUT_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File "
    "    {path: $file})-[r:IMPORTS]->(t) "
    "RETURN coalesce(t.path, t.name) AS target, "
    "       CASE WHEN t:File THEN 'file' ELSE t.kind END AS kind, "
    "       r.raw AS raw, r.line AS line "
    "ORDER BY r.line"
)

_IMPORTS_IN_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
    "-[r:IMPORTS]->(dst:File {path: $file}) "
    "RETURN src.path AS source_file, r.raw AS raw, r.line AS line "
    "ORDER BY src.path, r.line"
)

_GRAPH_STATUS_CYPHER = (
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
    "WITH count(DISTINCT f) AS files "
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES]->(c:Class) "
    "WITH files, count(DISTINCT c) AS classes "
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES]->(fn:Function) WHERE NOT fn:Test "
    "WITH files, classes, count(DISTINCT fn) AS functions "
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES]->(:Class)-[:HAS_METHOD]->(m:Method) "
    "WITH files, classes, functions, count(DISTINCT m) AS methods "
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES]->(t:Test) "
    "WITH files, classes, functions, methods, "
    "     count(DISTINCT t) AS tests "
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(:Function|:Method)"
    "-[ec:CALLS]->() "
    "WITH files, classes, functions, methods, tests, "
    "     count(DISTINCT ec) AS calls_total, "
    "     sum(CASE WHEN coalesce(ec.runtime,false) AND NOT coalesce(ec.static,false) "
    "              THEN 1 ELSE 0 END) AS calls_runtime_only, "
    "     sum(CASE WHEN coalesce(ec.static,false) AND NOT coalesce(ec.runtime,false) "
    "              THEN 1 ELSE 0 END) AS calls_static_only, "
    "     sum(CASE WHEN coalesce(ec.static,false) AND coalesce(ec.runtime,false) "
    "              THEN 1 ELSE 0 END) AS calls_both "
    "RETURN $project AS project, files, classes, functions, methods, "
    "       tests, calls_total, calls_runtime_only, "
    "       calls_static_only, calls_both"
)


def imports(backend: GraphBackend, project: str, file: str,
            direction: str = "out") -> list[dict[str, Any]]:
    """Imports out of (or into) ``file`` within the project."""
    if direction == "out":
        rows = backend.execute(
            _IMPORTS_OUT_CYPHER, project=project, file=file,
        )
        return [
            {"target": r["target"], "kind": r.get("kind") or "thirdparty",
             "raw": r.get("raw") or "", "line": int(r.get("line") or 0)}
            for r in rows
        ]
    if direction == "in":
        rows = backend.execute(
            _IMPORTS_IN_CYPHER, project=project, file=file,
        )
        return [
            {"source_file": r["source_file"],
             "raw": r.get("raw") or "", "line": int(r.get("line") or 0)}
            for r in rows
        ]
    raise ValueError(
        f"imports direction must be 'out' or 'in', got {direction!r}"
    )


_GRAPH_STATUS_KEYS = (
    "project", "files", "classes", "functions", "methods", "tests",
    "calls_total", "calls_runtime_only", "calls_static_only", "calls_both",
)


def graph_status(backend: GraphBackend,
                 project: str) -> dict[str, Any]:
    """Aggregate counts for the configured project."""
    rows = backend.execute(_GRAPH_STATUS_CYPHER, project=project)
    if not rows:
        return {
            "project": project,
            **{k: 0 for k in _GRAPH_STATUS_KEYS if k != "project"},
        }
    row = rows[0]
    return {
        "project": row.get("project") or project,
        **{k: int(row.get(k) or 0) for k in _GRAPH_STATUS_KEYS
           if k != "project"},
    }
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_structure.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools.py tests/unit/test_mcp_tools_structure.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: MCP tools imports and graph_status"
```

---

## Task 9: MCP server wiring (`server.py`)

**Files:**
- Create: `livegraph/mcp/server.py`
- Test: `tests/unit/test_mcp_server.py`

The server holds backend + project state in module-level globals (set in `bootstrap()`), then registers thin wrapper functions with FastMCP. The wrappers call `tools.<name>(...)` with those globals.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mcp_server.py
from livegraph.graph.backend import FakeBackend
from livegraph.mcp.server import bootstrap, build_server


def test_bootstrap_sets_state_and_returns_server():
    backend = FakeBackend()
    server = bootstrap(backend, project="sample")
    assert server is not None
    from livegraph.mcp import server as srv_mod
    assert srv_mod._BACKEND is backend
    assert srv_mod._PROJECT == "sample"


def test_build_server_registers_all_ten_tools():
    backend = FakeBackend()
    server = bootstrap(backend, project="sample")
    tool_names = sorted(_registered_tool_names(server))
    expected = sorted([
        "find_symbol", "get_source",
        "find_callers", "find_callees",
        "runtime_only_calls", "dead_static_calls",
        "tests_for", "untested_symbols",
        "imports", "graph_status",
    ])
    assert tool_names == expected


def _registered_tool_names(server) -> list[str]:
    # FastMCP exposes the registered tools via its internal tool manager.
    # The attribute name has been stable since mcp 1.0: `_tool_manager`.
    manager = getattr(server, "_tool_manager", None)
    if manager is None:
        manager = getattr(server, "tool_manager", None)
    assert manager is not None, "FastMCP server has no tool manager attr"
    # FastMCP's tool manager exposes `list_tools()` returning Tool objects.
    return [t.name for t in manager.list_tools()]
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_server.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.mcp.server'`.

- [ ] **Step 3: Write `livegraph/mcp/server.py`**

```python
"""FastMCP server that exposes livegraph's 10 read-only tools over stdio.

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
    """Construct a FastMCP server with all 10 tools registered.

    Tool wrappers reference the module-level state set by ``bootstrap``.
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
    def tests_for(qualified_name: str) -> list[dict[str, Any]]:
        """Tests that cover this symbol, with per-test coverage."""
        backend, project = _require_state()
        return tools.tests_for(backend, project,
                               qualified_name=qualified_name)

    @mcp.tool()
    def untested_symbols(file: str | None = None, kind: str = "any",
                         limit: int = 100) -> list[dict[str, Any]]:
        """Functions/methods the test suite never exercised."""
        backend, project = _require_state()
        return tools.untested_symbols(backend, project, file=file,
                                      kind=kind, limit=limit)

    @mcp.tool()
    def imports(file: str,
                direction: str = "out") -> list[dict[str, Any]]:
        """Outgoing (out) or incoming (in) imports for a file."""
        backend, project = _require_state()
        return tools.imports(backend, project, file=file,
                             direction=direction)

    @mcp.tool()
    def graph_status() -> dict[str, Any]:
        """Counts: files, symbols, tests, calls split by provenance."""
        backend, project = _require_state()
        return tools.graph_status(backend, project)

    return mcp


def bootstrap(backend: GraphBackend, project: str) -> FastMCP:
    """Initialize global state and return a configured FastMCP server."""
    global _BACKEND, _PROJECT
    _BACKEND = backend
    _PROJECT = project
    return build_server()


def run_stdio(backend: GraphBackend, project: str) -> None:
    """Launch the server and serve stdio until stdin closes."""
    server = bootstrap(backend, project)
    server.run()  # FastMCP defaults to stdio transport
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_mcp_server.py -v
```
Expected: 2 passed. If the second test fails with `AssertionError: FastMCP server has no tool manager attr`, the SDK has renamed the attribute — look at `dir(server)` for the manager, update `_registered_tool_names`, and report which attribute name is current.

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/server.py tests/unit/test_mcp_server.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: FastMCP server wiring for the 10 tools"
```

---

## Task 10: CLI `livegraph mcp` subcommand

**Files:**
- Modify: `livegraph/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Append the failing test to `tests/unit/test_cli.py`**

```python
def test_mcp_command_errors_when_project_missing(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    result = runner.invoke(cli.app, ["mcp"])
    assert result.exit_code != 0
    assert "LIVEGRAPH_PROJECT" in (result.output + (result.stderr or ""))


def test_mcp_command_invokes_run_stdio_with_project(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)

    captured: dict = {}

    def fake_run_stdio(b, project):
        captured["backend"] = b
        captured["project"] = project

    monkeypatch.setattr("livegraph.cli.run_stdio", fake_run_stdio)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "sample")
    result = runner.invoke(cli.app, ["mcp"])
    assert result.exit_code == 0
    assert captured["backend"] is backend
    assert captured["project"] == "sample"


def test_mcp_command_project_flag_overrides_env(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)

    captured: dict = {}

    def fake_run_stdio(b, project):
        captured["project"] = project

    monkeypatch.setattr("livegraph.cli.run_stdio", fake_run_stdio)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "fromenv")
    result = runner.invoke(cli.app, ["mcp", "--project", "fromflag"])
    assert result.exit_code == 0
    assert captured["project"] == "fromflag"
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_cli.py -v
```
Expected: FAIL — `Error: No such command 'mcp'.` on at least one test.

- [ ] **Step 3: Add to `livegraph/cli.py`**

Add this import near the top of `livegraph/cli.py` (after the existing `from livegraph.runtime.runner` import):

```python
from livegraph.mcp.server import run_stdio
```

Then add this command after the existing `build` command, before the `if __name__ == "__main__":` block:

```python
@app.command()
def mcp(
    project: str = typer.Option(
        None, "--project",
        help="Ingested project to serve (overrides LIVEGRAPH_PROJECT env)",
    ),
) -> None:
    """Run the MCP server over stdio."""
    settings = load_settings()
    resolved = project or settings.livegraph_project
    if not resolved:
        typer.echo(
            "LIVEGRAPH_PROJECT is not set. Pass --project NAME or set the "
            "LIVEGRAPH_PROJECT environment variable to the name of an "
            "ingested project.",
            err=True,
        )
        raise typer.Exit(code=2)
    backend = _make_backend()
    try:
        backend.verify()
    except ConnectionError as exc:
        typer.echo(f"Neo4j unreachable: {exc}", err=True)
        backend.close()
        raise typer.Exit(code=1) from exc
    try:
        run_stdio(backend, resolved)
    finally:
        backend.close()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_cli.py -v 2>&1 | tail -10
```
Expected: all 7 tests pass (4 existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add livegraph/cli.py tests/unit/test_cli.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: livegraph mcp CLI subcommand"
```

---

## Task 11: MCP integration fixtures + shared ingested setup

**Files:**
- Modify: `tests/integration/conftest.py`

Add an `ingested_sample` fixture that runs Phase 1 + Phase 2 against the sample fixture project, leaving the graph populated for every integration test below. This avoids re-ingesting in each test.

- [ ] **Step 1: Modify `tests/integration/conftest.py`**

Append this fixture to the existing `tests/integration/conftest.py`:

```python
@pytest.fixture()
def ingested_sample(neo4j_backend, sample_project_path):
    """Run Phase 1 + Phase 2 on the sample project; yield ``(backend, project_name)``."""
    import sys

    from livegraph.augment import augment_from_observations
    from livegraph.ingest import ingest_project
    from livegraph.runtime.runner import run_pytest

    project_name = "sample"
    ingest_project(sample_project_path, neo4j_backend,
                   project_name=project_name, batch_size=100)
    observations = run_pytest(sample_project_path, python=sys.executable)
    augment_from_observations(observations, neo4j_backend, batch_size=100)
    yield neo4j_backend, project_name
```

- [ ] **Step 2: Verify it loads (sanity)**

```bash
.venv/bin/pytest --collect-only tests/integration/ -q 2>&1 | tail -10
```
Expected: collection succeeds (no syntax errors, fixture name visible).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/conftest.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "test: ingested_sample integration fixture"
```

---

## Task 12: Cypher integration tests for all 10 tools

**Files:**
- Create: `tests/integration/test_mcp_tools_integration.py`

These run against real Neo4j (Phase 1+2 ingested into the sample project) and verify the Cypher actually produces the expected results — including the differentiator's `runtime_only_calls`.

- [ ] **Step 1: Write the integration tests**

```python
# tests/integration/test_mcp_tools_integration.py
"""End-to-end Cypher tests for the MCP tool functions."""
import pytest

from livegraph.mcp import tools

pytestmark = pytest.mark.integration


def test_find_symbol_substring_against_real_graph(ingested_sample):
    backend, project = ingested_sample
    results = tools.find_symbol(backend, project, query="run")
    qns = {r["qualified_name"] for r in results}
    assert "runner.py::run_operation" in qns


def test_get_source_returns_definition(ingested_sample):
    backend, project = ingested_sample
    result = tools.get_source(backend, project,
                              qualified_name="calculator.py::Calculator.add")
    assert result is not None
    assert result["name"] == "add"
    assert result["kind"] == "method"
    assert "def add" in result["source"]


def test_get_source_missing_returns_none(ingested_sample):
    backend, project = ingested_sample
    assert tools.get_source(backend, project,
                            qualified_name="nope.py::nope") is None


def test_find_callees_finds_static_call_in_main(ingested_sample):
    backend, project = ingested_sample
    results = tools.find_callees(backend, project,
                                 qualified_name="runner.py::main")
    callee_qns = {r["callee"]["qualified_name"] for r in results}
    # main calls run_operation statically.
    assert "runner.py::run_operation" in callee_qns


def test_find_callers_provenance_filter(ingested_sample):
    backend, project = ingested_sample
    # Calculator.add has only a runtime caller (run_operation via dynamic dispatch).
    runtime_only = tools.find_callers(
        backend, project,
        qualified_name="calculator.py::Calculator.add",
        provenance="runtime",
    )
    assert any(r["caller"]["qualified_name"] == "runner.py::run_operation"
               for r in runtime_only)


def test_runtime_only_calls_finds_dynamic_dispatch(ingested_sample):
    """The differentiator. If this passes, livegraph's premise is intact."""
    backend, project = ingested_sample
    results = tools.runtime_only_calls(backend, project)
    pairs = {(r["caller"]["qualified_name"],
              r["callee"]["qualified_name"]) for r in results}
    assert ("runner.py::run_operation",
            "calculator.py::Calculator.add") in pairs


def test_dead_static_calls_returns_static_only_edges(ingested_sample):
    backend, project = ingested_sample
    results = tools.dead_static_calls(backend, project)
    # The sample fixture is fully exercised by its tests, so this may be
    # empty. The contract is just that the call succeeds and returns a list.
    assert isinstance(results, list)


def test_tests_for_returns_tests_with_coverage(ingested_sample):
    backend, project = ingested_sample
    results = tools.tests_for(
        backend, project,
        qualified_name="calculator.py::Calculator.add",
    )
    assert results, "Calculator.add should be covered by at least one test"
    assert all("test_outcome" in r["test"] for r in results)
    assert all(0.0 <= r["coverage_pct"] <= 100.0 for r in results)


def test_untested_symbols_against_real_graph(ingested_sample):
    backend, project = ingested_sample
    # The sample fixture's tests exercise everything; assert the call works.
    results = tools.untested_symbols(backend, project, kind="function")
    assert isinstance(results, list)


def test_imports_out_returns_internal_and_external(ingested_sample):
    backend, project = ingested_sample
    results = tools.imports(backend, project, file="runner.py",
                            direction="out")
    targets = {r["target"] for r in results}
    # runner.py imports calculator (internal File)
    assert "calculator.py" in targets


def test_imports_in_returns_files_importing_this_one(ingested_sample):
    backend, project = ingested_sample
    results = tools.imports(backend, project, file="calculator.py",
                            direction="in")
    sources = {r["source_file"] for r in results}
    # runner.py and test_calculator.py both import calculator
    assert "runner.py" in sources


def test_graph_status_returns_expected_counts(ingested_sample):
    backend, project = ingested_sample
    status = tools.graph_status(backend, project)
    assert status["project"] == project
    assert status["files"] == 3
    # The fixture has exactly 1 class (Calculator) with 2 methods.
    assert status["classes"] == 1
    assert status["methods"] == 2
    # At least one runtime-only edge — the dynamic-dispatch one.
    assert status["calls_runtime_only"] >= 1
```

- [ ] **Step 2: Run integration tests**

Make sure Neo4j is up first:
```bash
brew services start neo4j 2>/dev/null || true
for i in $(seq 1 30); do
  (echo > /dev/tcp/localhost/7687) 2>/dev/null && break
  sleep 1
done
```

Then:
```bash
.venv/bin/pytest tests/integration/test_mcp_tools_integration.py -v -m integration 2>&1 | tail -20
```
Expected: 12 passed.

If any test fails, do NOT weaken assertions. Common debugging steps:
- `test_runtime_only_calls_finds_dynamic_dispatch` failing → check that `livegraph build /path/to/fixture` works manually and the differentiator query (`MATCH ... WHERE c.runtime AND coalesce(c.static,false)=false`) returns rows in Neo4j Browser.
- `test_graph_status` counts mismatch → run the constituent OPTIONAL MATCH clauses separately to find which count is off; the spec for the sample project says 3 files / 1 class / 2 methods (Calculator with `add` and `multiply`).
- `imports` tests failing → check that the Phase 1 `_write_imports` actually populated `r.raw` and `r.line` properties on the IMPORTS edge.

- [ ] **Step 3: Run the whole suite to confirm no regressions**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
.venv/bin/pytest -m integration -q 2>&1 | tail -3
```
Expected: all unit + integration tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_mcp_tools_integration.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "test: integration tests for all 10 MCP tools incl. differentiator"
```

---

## Task 13: MCP runtime smoke test

**Files:**
- Create: `tests/integration/test_mcp_server_smoke.py`

End-to-end test: launch the FastMCP server over an in-process stdio pipe via the MCP SDK's client, list the tools (assert all 10 with their schemas), and invoke `graph_status` over the wire.

- [ ] **Step 1: Write the smoke test**

```python
# tests/integration/test_mcp_server_smoke.py
"""Round-trip the MCP protocol against livegraph's FastMCP server."""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration


def test_mcp_server_lists_and_calls_tools_over_protocol(ingested_sample):
    """End-to-end: register tools, list them, call graph_status."""
    backend, project = ingested_sample
    from livegraph.mcp.server import bootstrap

    async def run() -> None:
        server = bootstrap(backend, project)
        manager = getattr(server, "_tool_manager", None) \
            or getattr(server, "tool_manager", None)
        assert manager is not None
        tool_names = sorted(t.name for t in manager.list_tools())
        assert tool_names == sorted([
            "find_symbol", "get_source",
            "find_callers", "find_callees",
            "runtime_only_calls", "dead_static_calls",
            "tests_for", "untested_symbols",
            "imports", "graph_status",
        ])
        # Invoke graph_status through the manager (same code path FastMCP
        # uses when an MCP client calls a tool).
        result = await manager.call_tool("graph_status", {})
        # FastMCP returns a CallToolResult-like object; depending on SDK
        # version it has `.content` (text-content list) or returns a dict.
        if hasattr(result, "structuredContent") and result.structuredContent:
            payload = result.structuredContent
        elif hasattr(result, "content"):
            import json
            payload = json.loads(result.content[0].text)
        else:
            payload = result
        assert payload["project"] == project
        assert payload["files"] == 3
        assert payload["calls_runtime_only"] >= 1

    asyncio.run(run())
```

- [ ] **Step 2: Run the smoke test**

```bash
.venv/bin/pytest tests/integration/test_mcp_server_smoke.py -v -m integration 2>&1 | tail -10
```
Expected: 1 passed. If `call_tool` is named differently in the installed `mcp` version (e.g. `_invoke_tool`), inspect `dir(manager)` and adjust. If the result wrapper differs, the three-way unwrap in the test (`structuredContent` → `content[0].text` → raw) is intentionally tolerant.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_mcp_server_smoke.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "test: MCP server runtime smoke test"
```

---

## Task 14: README MCP section + final verify

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append an MCP section to `README.md`**

Append this block at the end of `README.md`:

````markdown

## Using livegraph from a coding agent (MCP)

After `livegraph build /path/to/project`, expose the graph to a
coding agent over MCP:

```bash
LIVEGRAPH_PROJECT=myproject livegraph mcp
```

The server runs over stdio. Configure your MCP host (Claude Code, Cursor)
to launch it. Example `.mcp.json` for Claude Code:

```json
{
  "mcpServers": {
    "livegraph-myproject": {
      "command": "livegraph",
      "args": ["mcp", "--project", "myproject"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "livegraph-local"
      }
    }
  }
}
```

The server exposes 10 read-only tools, including the two that no
purely static code-graph tool can run:

| Tool | What it answers |
|---|---|
| `find_symbol(query)` | Symbols matching a name |
| `get_source(qualified_name)` | Source + coverage for a symbol |
| `find_callers(qualified_name, provenance)` | Who calls this — `static`/`runtime`/`any` |
| `find_callees(qualified_name, provenance)` | What this calls — same filter |
| **`runtime_only_calls(file?)`** | Calls runtime caught that static missed |
| `dead_static_calls(file?)` | Predicted calls that never executed |
| `tests_for(qualified_name)` | Tests that cover a symbol |
| `untested_symbols(file?, kind?)` | Functions/methods no test exercised |
| `imports(file, direction)` | File-level import edges |
| `graph_status()` | Aggregate counts; call this first |

**Acceptance test:** with the server registered, ask your agent
"show me the dynamic-dispatch calls in this project". A working
integration finds and calls `runtime_only_calls`.
````

- [ ] **Step 2: Final full-suite verify**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
.venv/bin/pytest -m integration -q 2>&1 | tail -3
.venv/bin/ruff check livegraph 2>&1 | tail -5
```
Expected: all unit tests pass; all integration tests pass; ruff clean. Fix any new ruff issues that come from this task's code (do not refactor anything outside `livegraph/mcp/` or `livegraph/cli.py`).

- [ ] **Step 3: Commit**

```bash
git add README.md
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "docs: add MCP server section to README"
```

---

## Done

After Task 14, `livegraph mcp --project NAME` launches a stdio MCP server exposing 10 read-only tools over a Phase 1+2 graph. The differentiator (`runtime_only_calls`) is reachable by any MCP host. The integration test suite proves the Cypher works against real Neo4j; the smoke test proves the protocol works against the real SDK.

Try it manually:

```bash
# In a livegraph project:
livegraph build /path/to/some/python/project   # Phase 1 + 2
LIVEGRAPH_PROJECT=<name> livegraph mcp          # serves stdio MCP

# Then configure your agent host (.mcp.json above) and ask it:
#   "Show me the dynamic-dispatch calls in this project."
# A working integration finds and calls runtime_only_calls.
```

Out of scope, as designed: NL→Cypher, embeddings, multi-language, incremental updates, write tools. Each is its own future spec.
