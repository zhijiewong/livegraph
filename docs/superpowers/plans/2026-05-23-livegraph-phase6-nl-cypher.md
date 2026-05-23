# livegraph Phase 6 — NL→Cypher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 6 — two new MCP tools (`describe_schema` and `run_cypher`) that let an agent's own LLM generate and run safe, read-only Cypher against the livegraph graph. Brings the MCP server tool count from 11 to 13.

**Architecture:** A new `livegraph/mcp/cypher_guard.py` module provides pure-function safety primitives — `forbidden_keyword()`, `auto_limit()`, `inject_project()` — plus typed exception classes for each error class. `GraphBackend` gains an `execute_read()` method that opens a Neo4j READ transaction with a configurable timeout. The `run_cypher` tool composes guard + backend.execute_read; the `describe_schema` tool returns a static-ish schema description with the configured project name injected. No LLM lives inside livegraph; the host agent's LLM generates the Cypher.

**Tech Stack:** Python 3.12+, no new runtime dependencies. Uses the existing `neo4j` driver's `session(default_access_mode="READ").execute_read(callback, timeout=...)` API for engine-enforced read mode and per-transaction timeouts.

**Reference:** Design spec at `docs/superpowers/specs/2026-05-23-livegraph-phase6-nl-cypher-design.md`.

**Conventions for every task:**
- Run tests from the repo root: `cd /Users/yvon.zhu/Documents/GitHub/livegraph`.
- Unit tests need no Neo4j. Integration tests are `@pytest.mark.integration` and need Neo4j up (`brew services start neo4j`).
- If git complains about identity, use `git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit ...`.
- All work happens on a feature branch (`implement-phase-6-nl-cypher`) created in Task 1.

---

## Task 1: Branch + sanity check

**Files:** None (branch only).

- [ ] **Step 1: Create the feature branch**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
git checkout main
git pull --ff-only
git checkout -b implement-phase-6-nl-cypher
```

- [ ] **Step 2: Sanity-check the existing suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 135 passed (the Phase 5 baseline), no errors.

## Report
Status (DONE / BLOCKED), exact pytest output, current branch.

---

## Task 2: Add `query_row_limit` and `query_timeout_seconds` to Settings

**Files:**
- Modify: `livegraph/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Append failing tests to `tests/unit/test_config.py`**:

```python
def test_query_row_limit_default(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_QUERY_ROW_LIMIT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.livegraph_query_row_limit == 1000


def test_query_row_limit_from_env(monkeypatch):
    monkeypatch.setenv("LIVEGRAPH_QUERY_ROW_LIMIT", "250")
    settings = Settings(_env_file=None)
    assert settings.livegraph_query_row_limit == 250


def test_query_timeout_default(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_QUERY_TIMEOUT_SECONDS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.livegraph_query_timeout_seconds == 30


def test_query_timeout_from_env(monkeypatch):
    monkeypatch.setenv("LIVEGRAPH_QUERY_TIMEOUT_SECONDS", "5")
    settings = Settings(_env_file=None)
    assert settings.livegraph_query_timeout_seconds == 5
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_config.py -v 2>&1 | tail -10
```
Expected: 4 new tests FAIL with `AttributeError: 'Settings' object has no attribute 'livegraph_query_row_limit'`.

- [ ] **Step 3: Add the two fields to `livegraph/config.py`**

In `livegraph/config.py`, inside the `Settings` class, after `livegraph_project: str | None = None`, add:

```python
    livegraph_query_row_limit: int = 1000
    livegraph_query_timeout_seconds: int = 30
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_config.py -v 2>&1 | tail -10
```
Expected: all config tests pass (existing + 4 new = 8 total).

- [ ] **Step 5: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 139 passed.

- [ ] **Step 6: Commit**

```bash
git add livegraph/config.py tests/unit/test_config.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: add query_row_limit and query_timeout_seconds settings"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 3: `cypher_guard.py` — safety pure functions and exceptions

**Files:**
- Create: `livegraph/mcp/cypher_guard.py`
- Test: `tests/unit/test_cypher_guard.py`

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_cypher_guard.py`:

```python
import pytest

from livegraph.mcp.cypher_guard import (
    forbidden_keyword, auto_limit, inject_project,
    ForbiddenKeywordError, CypherSyntaxError, CypherTimeoutError,
    EngineWriteAttemptedError,
)


# -- forbidden_keyword -------------------------------------------------

@pytest.mark.parametrize("kw", [
    "CREATE", "MERGE", "DELETE", "DETACH DELETE", "SET", "REMOVE",
    "DROP", "LOAD CSV", "USING PERIODIC COMMIT", "CALL",
])
def test_forbidden_keyword_detected(kw):
    query = f"MATCH (n) {kw} n.x = 1 RETURN n"
    assert forbidden_keyword(query) is not None


def test_forbidden_keyword_case_insensitive():
    assert forbidden_keyword("match (n) delete n") is not None
    assert forbidden_keyword("match (n) Delete n") is not None
    assert forbidden_keyword("match (n) DELETE n") is not None


def test_forbidden_keyword_word_boundary():
    # 'CREATEd' is not a keyword (the word boundary doesn't match).
    assert forbidden_keyword("MATCH (n) WHERE n.name = 'CREATEd' RETURN n") is None
    # But 'CREATE' as a separate word IS.
    assert forbidden_keyword("CREATE (n:X) RETURN n") is not None


def test_forbidden_keyword_returns_uppercased_name():
    assert forbidden_keyword("match (n) delete n") == "DELETE"


def test_forbidden_keyword_returns_none_for_safe_query():
    assert forbidden_keyword("MATCH (n) RETURN n LIMIT 10") is None


def test_forbidden_keyword_detects_detach_delete_as_whole():
    # Multi-word form with whitespace.
    assert forbidden_keyword("MATCH (n) DETACH DELETE n") in ("DETACH DELETE", "DELETE")


def test_forbidden_keyword_detects_load_csv():
    assert forbidden_keyword("LOAD CSV FROM 'x' AS row RETURN row") in ("LOAD CSV", "LOAD")


# -- auto_limit --------------------------------------------------------

def test_auto_limit_appends_when_missing():
    assert auto_limit("MATCH (n) RETURN n", 100) == "MATCH (n) RETURN n LIMIT 100"


def test_auto_limit_preserves_existing_limit():
    q = "MATCH (n) RETURN n LIMIT 5"
    assert auto_limit(q, 100) == q


def test_auto_limit_preserves_existing_limit_case_insensitive():
    q = "MATCH (n) RETURN n limit 5"
    assert auto_limit(q, 100) == q


def test_auto_limit_strips_trailing_semicolon():
    assert auto_limit("MATCH (n) RETURN n;", 50) == "MATCH (n) RETURN n LIMIT 50"


def test_auto_limit_strips_trailing_whitespace():
    assert auto_limit("MATCH (n) RETURN n   \n  ", 50) == "MATCH (n) RETURN n LIMIT 50"


# -- inject_project ----------------------------------------------------

def test_inject_project_when_params_none():
    assert inject_project(None, "sample") == {"project": "sample"}


def test_inject_project_when_omitted():
    assert inject_project({"q": "foo"}, "sample") == {"q": "foo", "project": "sample"}


def test_inject_project_preserves_caller_override():
    # Caller explicitly passed a project — must NOT be overridden.
    result = inject_project({"project": "other"}, "sample")
    assert result["project"] == "other"


# -- exception types ---------------------------------------------------

def test_forbidden_keyword_error_carries_query():
    err = ForbiddenKeywordError("DELETE", "MATCH (n) DELETE n")
    assert err.keyword == "DELETE"
    assert err.query == "MATCH (n) DELETE n"
    assert err.code == "forbidden_keyword"
    assert "DELETE" in str(err)


def test_cypher_syntax_error_carries_message():
    err = CypherSyntaxError("unexpected token", "MATCH x")
    assert err.code == "cypher_syntax"
    assert err.query == "MATCH x"


def test_cypher_timeout_error_carries_seconds():
    err = CypherTimeoutError(30, "MATCH (n) RETURN n")
    assert err.code == "timeout"
    assert "30" in str(err)


def test_engine_write_attempted_error_carries_query():
    err = EngineWriteAttemptedError("CREATE (n) RETURN n")
    assert err.code == "engine_write_attempted"
    assert err.query == "CREATE (n) RETURN n"
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_cypher_guard.py -v 2>&1 | tail -5
```
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.mcp.cypher_guard'`.

- [ ] **Step 3: Write `livegraph/mcp/cypher_guard.py`**

```python
"""Safety pipeline for agent-submitted Cypher queries.

Pure functions: ``forbidden_keyword``, ``auto_limit``, ``inject_project``.
Typed exception classes for each error class that ``run_cypher`` can raise.
The actual read-transaction execution lives on the GraphBackend so this
module stays dependency-free and trivially unit-testable.
"""
from __future__ import annotations

import re
from typing import Any

# Rejects write clauses, schema management, bulk-load, and any procedure call.
# Word-boundary anchored so identifiers like 'CREATEd' are not matched.
# Multi-word forms tolerate whitespace.
_FORBIDDEN = re.compile(
    r"\b(DETACH\s+DELETE|LOAD\s+CSV|USING\s+PERIODIC\s+COMMIT|"
    r"CREATE|MERGE|DELETE|SET|REMOVE|DROP|CALL)\b",
    re.IGNORECASE,
)

# A query already has a "LIMIT N" tail if it ends with LIMIT, a number,
# optional semicolon, optional whitespace.
_TRAILING_LIMIT = re.compile(r"\bLIMIT\b\s+\d+\s*;?\s*$", re.IGNORECASE)


def forbidden_keyword(query: str) -> str | None:
    """Return the first forbidden keyword found, uppercased, or None."""
    match = _FORBIDDEN.search(query)
    if match is None:
        return None
    return " ".join(match.group(1).upper().split())


def auto_limit(query: str, row_limit: int) -> str:
    """Append ``LIMIT row_limit`` if the query has no trailing LIMIT clause."""
    if _TRAILING_LIMIT.search(query) is not None:
        return query
    stripped = query.rstrip().rstrip(";").rstrip()
    return f"{stripped} LIMIT {row_limit}"


def inject_project(params: dict[str, Any] | None,
                   project: str) -> dict[str, Any]:
    """Return a copy of ``params`` with ``$project`` defaulted (not overridden)."""
    out = dict(params or {})
    out.setdefault("project", project)
    return out


# -- typed errors ------------------------------------------------------


class CypherError(Exception):
    """Base class for errors run_cypher can return to the caller."""

    code: str = "cypher_error"


class ForbiddenKeywordError(CypherError):
    code = "forbidden_keyword"

    def __init__(self, keyword: str, query: str) -> None:
        super().__init__(f"forbidden_keyword: {keyword}")
        self.keyword = keyword
        self.query = query


class CypherSyntaxError(CypherError):
    code = "cypher_syntax"

    def __init__(self, message: str, query: str) -> None:
        super().__init__(f"cypher_syntax: {message}")
        self.message = message
        self.query = query


class CypherTimeoutError(CypherError):
    code = "timeout"

    def __init__(self, seconds: int, query: str) -> None:
        super().__init__(f"timeout: query exceeded {seconds}s")
        self.seconds = seconds
        self.query = query


class EngineWriteAttemptedError(CypherError):
    code = "engine_write_attempted"

    def __init__(self, query: str) -> None:
        super().__init__("engine_write_attempted")
        self.query = query
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_cypher_guard.py -v 2>&1 | tail -25
```
Expected: all tests pass (approximately 23 tests including the parametrized cases).

If the `DETACH DELETE` test fails because the regex matches `DELETE` first (substring vs full multi-word), confirm the regex puts `DETACH\s+DELETE` *before* `DELETE` in the alternation — the regex tries alternatives left-to-right. The implementation above already lists multi-word forms first.

- [ ] **Step 5: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 139 + ~23 ≈ 162 passed.

- [ ] **Step 6: Commit**

```bash
git add livegraph/mcp/cypher_guard.py tests/unit/test_cypher_guard.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: cypher_guard safety primitives and exception types"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 4: `GraphBackend.execute_read()` — read transaction with timeout

**Files:**
- Modify: `livegraph/graph/backend.py`
- Test: `tests/unit/test_backend.py`

- [ ] **Step 1: Append failing tests to `tests/unit/test_backend.py`**:

```python
def test_fake_backend_execute_read_returns_rows_and_summary():
    from livegraph.graph.backend import FakeBackend
    backend = FakeBackend(rows=[{"qualified_name": "a.py::f"}])
    records, summary = backend.execute_read(
        "MATCH (n) RETURN n", project="sample",
    )
    assert records == [{"qualified_name": "a.py::f"}]
    assert summary["query_type"] == "read"
    assert "available_after_ms" in summary
    assert "consumed_after_ms" in summary


def test_fake_backend_execute_read_records_call():
    from livegraph.graph.backend import FakeBackend
    backend = FakeBackend()
    backend.execute_read("MATCH (n) RETURN n", timeout_seconds=10,
                         project="sample")
    cypher, params = backend.calls[0]
    assert cypher == "MATCH (n) RETURN n"
    # timeout_seconds is NOT a Cypher parameter — it controls the transaction.
    # It should not appear in params.
    assert "timeout_seconds" not in params
    assert params == {"project": "sample"}


def test_fake_backend_execute_read_default_timeout():
    from livegraph.graph.backend import FakeBackend
    backend = FakeBackend()
    # Default timeout_seconds=30 should be accepted without error.
    records, _summary = backend.execute_read("MATCH (n) RETURN n")
    assert records == []
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_backend.py -v 2>&1 | tail -10
```
Expected: 3 new tests FAIL with `AttributeError: 'FakeBackend' object has no attribute 'execute_read'`.

- [ ] **Step 3: Modify `livegraph/graph/backend.py`**

Add `execute_read` to the `GraphBackend` Protocol (after `execute`):

```python
    def execute_read(
        self, cypher: str, timeout_seconds: int = 30,
        **params: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Run a Cypher query in a READ transaction.

        Returns ``(records, summary)``. ``summary`` is a dict with
        ``available_after_ms``, ``consumed_after_ms``, and ``query_type``.
        Engine-enforced read mode: any write clause that bypassed lexical
        scanning is rejected here.
        """
```

Add the Neo4j implementation inside `Neo4jBackend`:

```python
    def execute_read(
        self, cypher: str, timeout_seconds: int = 30,
        **params: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from datetime import timedelta

        def _work(tx: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            result = tx.run(cypher, **params)
            records = [record.data() for record in result]
            consumed = result.consume()
            summary = {
                "available_after_ms": consumed.result_available_after or 0,
                "consumed_after_ms": consumed.result_consumed_after or 0,
                "query_type": "read",
            }
            return records, summary

        with self._driver.session(
            database=self._database, default_access_mode="READ",
        ) as session:
            return session.execute_read(
                _work, timeout=timedelta(seconds=timeout_seconds),
            )
```

Add the fake implementation inside `FakeBackend`:

```python
    def execute_read(
        self, cypher: str, timeout_seconds: int = 30,
        **params: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        self.calls.append((cypher, params))
        return list(self._rows), {
            "available_after_ms": 0,
            "consumed_after_ms": 0,
            "query_type": "read",
        }
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_backend.py -v 2>&1 | tail -10
```
Expected: all 5 backend tests pass (2 existing + 3 new).

- [ ] **Step 5: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 165 passed.

- [ ] **Step 6: Commit**

```bash
git add livegraph/graph/backend.py tests/unit/test_backend.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: GraphBackend.execute_read for read-transactions with timeout"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 5: `describe_schema` tool

**Files:**
- Modify: `livegraph/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools_describe_schema.py`

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_mcp_tools_describe_schema.py`:

```python
from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import describe_schema


def test_describe_schema_returns_documented_top_level_keys():
    backend = FakeBackend()
    result = describe_schema(backend, project="sample")
    assert result["project"] == "sample"
    assert "neo4j_version" in result
    assert "node_labels" in result
    assert "edge_types" in result
    assert "safety" in result
    assert "example_queries" in result


def test_describe_schema_node_labels_cover_every_kind():
    backend = FakeBackend()
    result = describe_schema(backend, project="sample")
    labels = set(result["node_labels"])
    expected = {"Project", "File", "Class", "Function", "Method",
                "Test", "Module"}
    assert expected <= labels


def test_describe_schema_edge_types_cover_every_relation():
    backend = FakeBackend()
    result = describe_schema(backend, project="sample")
    edges = set(result["edge_types"])
    expected = {"CONTAINS", "DEFINES", "HAS_METHOD",
                "IMPORTS", "CALLS", "COVERS"}
    assert expected <= edges


def test_describe_schema_safety_advertises_read_only():
    backend = FakeBackend()
    result = describe_schema(backend, project="sample")
    safety = result["safety"]
    assert safety["read_only"] is True
    assert "CREATE" in safety["forbidden_keywords"]
    assert "CALL" in safety["forbidden_keywords"]
    assert safety["row_limit_default"] == 1000
    assert safety["timeout_seconds_default"] == 30
    assert safety["project_auto_injected"] is True
    assert "$project" in safety["convention"]


def test_describe_schema_examples_cover_six_intents():
    backend = FakeBackend()
    result = describe_schema(backend, project="sample")
    intents = [ex["intent"] for ex in result["example_queries"]]
    assert len(intents) == 6
    # The dynamic-dispatch example must be present — it's the differentiator.
    assert any("Dynamic-dispatch" in i for i in intents)
    # Each example carries a Cypher string and a params_hint dict.
    for ex in result["example_queries"]:
        assert "query" in ex and isinstance(ex["query"], str)
        assert "params_hint" in ex and isinstance(ex["params_hint"], dict)


def test_describe_schema_does_not_call_backend():
    """describe_schema is static; it must not touch the backend."""
    backend = FakeBackend()
    describe_schema(backend, project="sample")
    assert backend.calls == []
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_describe_schema.py -v 2>&1 | tail -10
```
Expected: FAIL — `ImportError: cannot import name 'describe_schema'`.

- [ ] **Step 3: Append to `livegraph/mcp/tools.py`**

Add at the bottom of `livegraph/mcp/tools.py`:

```python
# -- describe_schema --------------------------------------------------

_NEO4J_VERSION_HINT = "5.x"

_NODE_LABELS_DESCRIPTION: dict[str, dict[str, Any]] = {
    "Project":  {"key": "name",
                 "properties": ["name", "root_path"]},
    "File":     {"key": "path",
                 "properties": ["path", "name", "language",
                                "parse_error", "content_hash"]},
    "Class":    {"key": "qualified_name",
                 "properties": ["qualified_name", "name", "file",
                                "start_line", "end_line",
                                "decorators", "source"]},
    "Function": {"key": "qualified_name",
                 "properties": ["qualified_name", "name", "file",
                                "start_line", "end_line",
                                "decorators", "source",
                                "runtime_observed", "coverage_pct",
                                "runtime_stale",
                                "test_outcome", "test_duration"]},
    "Method":   {"key": "qualified_name",
                 "properties": ["qualified_name", "name", "file",
                                "start_line", "end_line",
                                "decorators", "source",
                                "runtime_observed", "coverage_pct",
                                "runtime_stale"]},
    "Test":     {"note": ("An additional label on Function nodes "
                          "(test functions covered by livegraph trace). "
                          "Test nodes also satisfy :Function.")},
    "Module":   {"key": "name",
                 "properties": ["name", "kind"]},
}

_EDGE_TYPES_DESCRIPTION: dict[str, dict[str, Any]] = {
    "CONTAINS":   {"from": "Project|File", "to": "File",
                   "properties": []},
    "DEFINES":    {"from": "File", "to": "Class|Function",
                   "properties": []},
    "HAS_METHOD": {"from": "Class", "to": "Method", "properties": []},
    "IMPORTS":    {"from": "File", "to": "File|Module",
                   "properties": ["raw", "line"]},
    "CALLS":      {"from": "Function|Method", "to": "Function|Method",
                   "properties": ["static", "runtime",
                                  "observed_count", "call_site_lines"],
                   "note": ("Provenance flags: c.static=true means AST "
                            "predicted the call; c.runtime=true means it "
                            "was observed executing. "
                            "(static=false, runtime=true) is the "
                            "dynamic-dispatch differentiator.")},
    "COVERS":     {"from": "Test", "to": "Function|Method",
                   "properties": ["lines_covered", "lines_total",
                                  "coverage_pct"]},
}

_SAFETY_DESCRIPTION: dict[str, Any] = {
    "read_only": True,
    "forbidden_keywords": ["CREATE", "MERGE", "DELETE", "DETACH DELETE",
                           "SET", "REMOVE", "DROP", "LOAD CSV",
                           "USING PERIODIC COMMIT", "CALL"],
    "row_limit_default": 1000,
    "timeout_seconds_default": 30,
    "project_auto_injected": True,
    "convention": ("Every query should scope through "
                   "(:Project {name: $project})-[:CONTAINS]->(:File)->... ; "
                   "the $project parameter is injected automatically."),
}

_EXAMPLE_QUERIES: list[dict[str, Any]] = [
    {
        "intent": "Find a symbol by name",
        "query": (
            "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
            "-[:DEFINES|HAS_METHOD*1..2]->(s) "
            "WHERE toLower(s.name) CONTAINS toLower($q) "
            "RETURN s.qualified_name, s.name, labels(s), "
            "       s.file, s.start_line "
            "LIMIT 20"
        ),
        "params_hint": {"q": "<search term>"},
    },
    {
        "intent": "Find who calls a symbol",
        "query": (
            "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
            "-[:DEFINES|HAS_METHOD*1..2]->(callee) "
            "WHERE callee.qualified_name = $qn "
            "MATCH (caller)-[c:CALLS]->(callee) "
            "RETURN caller.qualified_name, c.static, c.runtime, "
            "       c.observed_count"
        ),
        "params_hint": {"qn": "<qualified_name>"},
    },
    {
        "intent": ("Dynamic-dispatch calls — runtime caught what static "
                   "missed"),
        "query": (
            "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
            "-[:DEFINES|HAS_METHOD*1..2]->(caller)"
            "-[c:CALLS]->(callee) "
            "WHERE c.runtime = true "
            "  AND coalesce(c.static, false) = false "
            "RETURN caller.qualified_name, callee.qualified_name, "
            "       c.observed_count "
            "LIMIT 50"
        ),
        "params_hint": {},
    },
    {
        "intent": "Tests that cover a symbol",
        "query": (
            "MATCH (s {qualified_name: $qn}) "
            "MATCH (t:Test)-[c:COVERS]->(s) "
            "RETURN t.qualified_name, c.coverage_pct, "
            "       c.lines_covered, c.lines_total "
            "ORDER BY c.coverage_pct DESC"
        ),
        "params_hint": {"qn": "<qualified_name>"},
    },
    {
        "intent": "Untested functions/methods",
        "query": (
            "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
            "-[:DEFINES|HAS_METHOD*1..2]->(s) "
            "WHERE (s:Function OR s:Method) AND NOT s:Test "
            "  AND coalesce(s.runtime_observed, false) = false "
            "RETURN s.qualified_name, s.file "
            "LIMIT 100"
        ),
        "params_hint": {},
    },
    {
        "intent": "Files that import a given file",
        "query": (
            "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
            "-[r:IMPORTS]->(dst:File {path: $file}) "
            "RETURN src.path, r.raw, r.line "
            "ORDER BY src.path"
        ),
        "params_hint": {"file": "<relative path>"},
    },
]


def describe_schema(backend: GraphBackend,
                    project: str) -> dict[str, Any]:
    """Return the static schema description for the configured project.

    No backend reads — every field is statically derived from the
    livegraph schema. The agent caches the response per session.
    """
    # ``backend`` is intentionally unused; we accept it to keep the
    # signature consistent with every other tool function in this module.
    _ = backend
    return {
        "project": project,
        "neo4j_version": _NEO4J_VERSION_HINT,
        "node_labels": _NODE_LABELS_DESCRIPTION,
        "edge_types": _EDGE_TYPES_DESCRIPTION,
        "safety": _SAFETY_DESCRIPTION,
        "example_queries": _EXAMPLE_QUERIES,
    }
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_describe_schema.py -v 2>&1 | tail -12
```
Expected: 6 passed.

- [ ] **Step 5: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 171 passed.

- [ ] **Step 6: Commit**

```bash
git add livegraph/mcp/tools.py tests/unit/test_mcp_tools_describe_schema.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: describe_schema MCP tool returns static schema + examples"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 6: `run_cypher` tool (the orchestrator)

**Files:**
- Modify: `livegraph/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools_run_cypher.py`

This tool composes `cypher_guard` primitives + `backend.execute_read`. Tests use a custom queued backend (similar to Phase 4's pattern) so we can drive both success and error paths.

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_mcp_tools_run_cypher.py`:

```python
from typing import Any

import pytest

from livegraph.mcp.cypher_guard import (
    CypherSyntaxError as GuardSyntaxError,
    CypherTimeoutError, EngineWriteAttemptedError, ForbiddenKeywordError,
)
from livegraph.mcp.tools import run_cypher


class _FakeBackend:
    """Test backend driving execute_read with success or error injection."""

    def __init__(self, *, records: list[dict[str, Any]] | None = None,
                 raise_exc: Exception | None = None) -> None:
        self._records = list(records or [])
        self._raise = raise_exc
        self.calls: list[tuple[str, dict[str, Any], int]] = []

    def verify(self) -> None:
        return None

    def execute(self, cypher, **params):
        # Not used by run_cypher; satisfies the protocol for FakeBackend
        # callers in the rest of the suite.
        return []

    def execute_read(self, cypher: str, timeout_seconds: int = 30,
                     **params):
        self.calls.append((cypher, dict(params), timeout_seconds))
        if self._raise is not None:
            raise self._raise
        return list(self._records), {
            "available_after_ms": 1, "consumed_after_ms": 2,
            "query_type": "read",
        }

    def close(self) -> None:
        return None


def test_run_cypher_returns_rows_and_summary():
    backend = _FakeBackend(records=[{"q": "a.py::f"}])
    result = run_cypher(backend, project="sample",
                       query="MATCH (n) RETURN n LIMIT 5")
    assert result["rows"] == [{"q": "a.py::f"}]
    assert result["row_count"] == 1
    assert result["truncated"] is False
    assert result["summary"]["query_type"] == "read"


def test_run_cypher_rejects_forbidden_keyword():
    backend = _FakeBackend()
    with pytest.raises(ForbiddenKeywordError) as exc:
        run_cypher(backend, project="sample",
                   query="MATCH (n) DELETE n")
    assert exc.value.keyword == "DELETE"
    assert exc.value.query == "MATCH (n) DELETE n"
    # Backend must not have been called.
    assert backend.calls == []


def test_run_cypher_injects_project_param():
    backend = _FakeBackend()
    run_cypher(backend, project="sample",
               query="MATCH (n) RETURN n LIMIT 1")
    _q, params, _t = backend.calls[0]
    assert params["project"] == "sample"


def test_run_cypher_caller_can_override_project():
    backend = _FakeBackend()
    run_cypher(backend, project="sample",
               query="MATCH (n) RETURN n LIMIT 1",
               params={"project": "other"})
    _q, params, _t = backend.calls[0]
    assert params["project"] == "other"


def test_run_cypher_auto_appends_limit_when_missing():
    backend = _FakeBackend()
    run_cypher(backend, project="sample",
               query="MATCH (n) RETURN n", row_limit=42)
    sent_cypher, _params, _t = backend.calls[0]
    assert sent_cypher.endswith("LIMIT 42")


def test_run_cypher_preserves_caller_limit():
    backend = _FakeBackend()
    run_cypher(backend, project="sample",
               query="MATCH (n) RETURN n LIMIT 7", row_limit=42)
    sent_cypher, _params, _t = backend.calls[0]
    # The caller's LIMIT 7 is preserved.
    assert sent_cypher.endswith("LIMIT 7")


def test_run_cypher_passes_timeout_seconds_to_backend():
    backend = _FakeBackend()
    run_cypher(backend, project="sample",
               query="MATCH (n) RETURN n LIMIT 1", timeout_seconds=11)
    _q, _params, timeout = backend.calls[0]
    assert timeout == 11


def test_run_cypher_truncates_when_rows_exceed_row_limit():
    backend = _FakeBackend(records=[{"i": i} for i in range(10)])
    result = run_cypher(backend, project="sample",
                       query="MATCH (n) RETURN n LIMIT 9999",
                       row_limit=3)
    assert result["row_count"] == 3
    assert result["truncated"] is True
    assert result["rows"] == [{"i": 0}, {"i": 1}, {"i": 2}]


def test_run_cypher_propagates_engine_write_attempted():
    backend = _FakeBackend(
        raise_exc=EngineWriteAttemptedError("CREATE (n) RETURN n"),
    )
    with pytest.raises(EngineWriteAttemptedError):
        # Note: this query passes the lexer because the forbidden
        # keyword is in a string literal. In production the lexer
        # would reject it, but the backend layer is the second gate.
        # Here we directly inject the error to verify propagation.
        run_cypher(backend, project="sample",
                   query="MATCH (n) WHERE n.body CONTAINS '_CREATE_' RETURN n")


def test_run_cypher_maps_neo4j_syntax_error():
    # The backend raises a generic exception that looks like a
    # neo4j.exceptions.CypherSyntaxError (matching by class name in
    # production). We exercise the mapping path here with a stand-in.
    class _FakeSyntaxError(Exception):
        message = "Invalid input 'X'"

    backend = _FakeBackend(raise_exc=_FakeSyntaxError("Invalid input 'X'"))
    with pytest.raises(GuardSyntaxError):
        run_cypher(backend, project="sample",
                   query="MATCH X RETURN X")


def test_run_cypher_maps_timeout_error():
    class _FakeTimeoutError(Exception):
        message = "Transaction timed out"

    backend = _FakeBackend(raise_exc=_FakeTimeoutError("timed out"))
    with pytest.raises(CypherTimeoutError):
        run_cypher(backend, project="sample",
                   query="MATCH (n) RETURN n LIMIT 1",
                   timeout_seconds=1)
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_run_cypher.py -v 2>&1 | tail -15
```
Expected: FAIL — `ImportError: cannot import name 'run_cypher'`.

- [ ] **Step 3: Append to `livegraph/mcp/tools.py`**

Add this import block near the top of `livegraph/mcp/tools.py` (after the existing imports):

```python
from livegraph.mcp.cypher_guard import (
    CypherSyntaxError, CypherTimeoutError,
    EngineWriteAttemptedError, ForbiddenKeywordError,
    auto_limit, forbidden_keyword, inject_project,
)
```

Then append at the end of `livegraph/mcp/tools.py`:

```python
# -- run_cypher -------------------------------------------------------

# Class-name fragments used to categorize neo4j driver exceptions
# without importing the driver at module load time. We match on
# ``type(exc).__name__`` to stay loosely coupled to the driver version.
_SYNTAX_ERROR_NAMES = {"CypherSyntaxError", "InvalidInput"}
_WRITE_ERROR_CODES = (
    "Neo.ClientError.Statement.AccessMode",
    "Neo.ClientError.Statement.SemanticError",
)
_TIMEOUT_NAME_FRAGMENTS = ("Timeout", "TimedOut")


def _categorize_backend_error(exc: Exception, query: str,
                              timeout_seconds: int) -> Exception:
    """Map a backend exception to a typed cypher_guard error."""
    name = type(exc).__name__
    message = str(exc)
    code = getattr(exc, "code", "") or ""

    if name in _SYNTAX_ERROR_NAMES or "SyntaxError" in name:
        return CypherSyntaxError(message, query)
    if any(t in name for t in _TIMEOUT_NAME_FRAGMENTS) or "timed out" in message.lower():
        return CypherTimeoutError(timeout_seconds, query)
    if code in _WRITE_ERROR_CODES or "writes" in message.lower():
        return EngineWriteAttemptedError(query)
    # Unknown error — bubble up as-is.
    return exc


def run_cypher(
    backend: GraphBackend, project: str, query: str,
    params: dict[str, Any] | None = None,
    row_limit: int = 1000, timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Run a read-only Cypher query for an agent.

    Pipeline: lexical pre-scan -> $project injection -> auto-LIMIT ->
    READ transaction -> truncate -> return ``{rows, truncated, row_count,
    summary}``. Each failure surfaces as a typed ``CypherError`` subclass.
    """
    # 1. Lexical pre-scan.
    kw = forbidden_keyword(query)
    if kw is not None:
        raise ForbiddenKeywordError(kw, query)

    # 2. Inject $project (caller can override).
    final_params = inject_project(params, project)

    # 3. Auto-append LIMIT if missing.
    final_query = auto_limit(query, row_limit)

    # 4 + 5. READ transaction with timeout.
    try:
        records, summary = backend.execute_read(
            final_query, timeout_seconds=timeout_seconds, **final_params,
        )
    except Exception as exc:
        raise _categorize_backend_error(exc, final_query, timeout_seconds) \
            from exc

    # 6. Truncate.
    truncated = len(records) > row_limit
    if truncated:
        records = records[:row_limit]

    return {
        "rows": records,
        "truncated": truncated,
        "row_count": len(records),
        "summary": summary,
    }
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_run_cypher.py -v 2>&1 | tail -15
```
Expected: 11 passed.

- [ ] **Step 5: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 182 passed.

- [ ] **Step 6: Commit**

```bash
git add livegraph/mcp/tools.py tests/unit/test_mcp_tools_run_cypher.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: run_cypher MCP tool with belt-and-suspenders safety"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 7: Register both tools with FastMCP (tool count 11 → 13)

**Files:**
- Modify: `livegraph/mcp/server.py`
- Test: `tests/unit/test_mcp_server.py`

- [ ] **Step 1: Update the existing tool-count test** in `tests/unit/test_mcp_server.py`. Find `test_build_server_registers_eleven_tools_including_change_impact` (created in Phase 4). Replace its expected list and rename it:

```python
def test_build_server_registers_thirteen_tools_including_describe_and_run():
    backend = FakeBackend()
    server = bootstrap(backend, project="sample")
    tool_names = sorted(_registered_tool_names(server))
    expected = sorted([
        "find_symbol", "get_source",
        "find_callers", "find_callees",
        "runtime_only_calls", "dead_static_calls",
        "tests_for", "untested_symbols",
        "imports", "graph_status",
        "change_impact",
        "describe_schema", "run_cypher",
    ])
    assert tool_names == expected
```

- [ ] **Step 2: Run failing test**

```bash
.venv/bin/pytest tests/unit/test_mcp_server.py -v 2>&1 | tail -10
```
Expected: 1 failure — `describe_schema`/`run_cypher` not in registered tool list.

- [ ] **Step 3: Add the wrappers to `livegraph/mcp/server.py`**

Inside `build_server()` in `livegraph/mcp/server.py`, after the existing `change_impact` tool and before the `return mcp` line, add:

```python
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
        row_limit: int = 1000, timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Run a read-only Cypher query against the project's graph.

        - ``query``: Cypher string. ``$project`` is auto-injected unless
          ``params`` overrides it.
        - ``params``: parameter map; any forbidden keyword (CREATE, MERGE,
          DELETE, SET, REMOVE, DROP, LOAD CSV, USING PERIODIC COMMIT,
          CALL) is rejected before execution.
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
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_server.py -v 2>&1 | tail -8
```
Expected: all server tests pass; tool count is 13.

- [ ] **Step 5: Update the Phase 3 smoke test** to expect 13 tools

Open `tests/integration/test_mcp_server_smoke.py`. Find the assertion block listing the expected tool names. Add `"describe_schema"` and `"run_cypher"` to the list, alongside the existing entries:

```python
        assert tool_names == sorted([
            "find_symbol", "get_source",
            "find_callers", "find_callees",
            "runtime_only_calls", "dead_static_calls",
            "tests_for", "untested_symbols",
            "imports", "graph_status",
            "change_impact",
            "describe_schema", "run_cypher",
        ])
```

- [ ] **Step 6: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 182 passed.

- [ ] **Step 7: Commit**

```bash
git add livegraph/mcp/server.py tests/unit/test_mcp_server.py tests/integration/test_mcp_server_smoke.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: register describe_schema and run_cypher (tool count 11 -> 13)"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 8: Integration tests against real Neo4j

**Files:**
- Create: `tests/integration/test_cypher_query_integration.py`

Reuses Phase 3's `ingested_sample` fixture. Six scenarios per design §9.

- [ ] **Step 1: Verify Neo4j is up**

```bash
(echo > /dev/tcp/localhost/7687) 2>/dev/null && echo "neo4j up" || (echo "neo4j DOWN" && brew services start neo4j && for i in $(seq 1 30); do (echo > /dev/tcp/localhost/7687) 2>/dev/null && echo "up after ${i}s" && break; sleep 1; done)
```

- [ ] **Step 2: Write the integration tests** at `tests/integration/test_cypher_query_integration.py`:

```python
"""End-to-end tests for describe_schema and run_cypher against real Neo4j."""
import pytest

from livegraph.mcp.cypher_guard import (
    CypherTimeoutError, EngineWriteAttemptedError,
)
from livegraph.mcp import tools

pytestmark = pytest.mark.integration


def test_describe_schema_roundtrip_against_real_graph(ingested_sample):
    """Every node label in describe_schema must appear in the actual graph."""
    backend, project = ingested_sample
    schema = tools.describe_schema(backend, project)
    declared_labels = set(schema["node_labels"]) - {"Test"}
    # Test is a secondary label; it may appear via :Function nodes that
    # happen to also be tests. We assert the primary labels only.

    actual_rows = backend.execute(
        "CALL db.labels() YIELD label RETURN collect(label) AS labels"
    )
    actual_labels = set(actual_rows[0]["labels"]) if actual_rows else set()
    # Every declared primary label is present in the real graph.
    assert declared_labels <= actual_labels


def test_run_cypher_dynamic_dispatch_example_finds_differentiator(
    ingested_sample,
):
    """Phase 6 acceptance test.

    Running the dynamic-dispatch example query verbatim must surface
    the runner.py::run_operation -> calculator.py::Calculator.add edge
    that no static-only code-graph tool can produce.
    """
    backend, project = ingested_sample
    schema = tools.describe_schema(backend, project)
    dyn_example = next(
        ex for ex in schema["example_queries"]
        if "Dynamic-dispatch" in ex["intent"]
    )
    result = tools.run_cypher(backend, project, query=dyn_example["query"])
    pairs = {
        (row["caller.qualified_name"], row["callee.qualified_name"])
        for row in result["rows"]
    }
    assert ("runner.py::run_operation",
            "calculator.py::Calculator.add") in pairs


def test_run_cypher_read_transaction_blocks_write(ingested_sample):
    """A write query that bypasses the lexer must be rejected at the engine."""
    backend, project = ingested_sample
    # We call backend.execute_read directly with a CREATE query —
    # bypassing the lexer to verify the read-transaction enforcement.
    from datetime import timedelta  # noqa: F401 (driver imports it)
    raised = False
    try:
        backend.execute_read("CREATE (n:_PhaseSixProbe) RETURN n",
                             timeout_seconds=10)
    except Exception as exc:
        # Engine refuses with some flavor of write-mode error.
        raised = True
        assert ("write" in str(exc).lower()
                or "access" in str(exc).lower()
                or "read" in str(exc).lower()
                or "ForbiddenAction" in type(exc).__name__)
    assert raised, "engine should have refused the write inside a read tx"

    # And confirm no _PhaseSixProbe node was created (defense in depth).
    rows = backend.execute(
        "MATCH (n:_PhaseSixProbe) RETURN count(n) AS n",
    )
    assert rows[0]["n"] == 0


def test_run_cypher_timeout_fires(ingested_sample):
    """A cartesian-product query against a tight timeout must time out."""
    backend, project = ingested_sample
    # Three-way cartesian product is O(n^3) on node count. On the sample
    # fixture this is fast in absolute terms — we use a 1-millisecond
    # timeout to make sure something fires.
    with pytest.raises(CypherTimeoutError):
        tools.run_cypher(
            backend, project,
            query=("MATCH (a) MATCH (b) MATCH (c) "
                   "WITH a, b, c LIMIT 10000 "
                   "RETURN count(*) AS n"),
            timeout_seconds=0,   # immediate timeout — should always fire
        )


def test_run_cypher_truncated_flag_set(ingested_sample):
    """A query that returns many rows must surface the truncated flag."""
    backend, project = ingested_sample
    # MATCH (n) RETURN n with a high inner LIMIT, run_cypher truncates at
    # the row_limit we pass. We pick a row_limit smaller than the node
    # count of the sample fixture (~10 symbols + 3 files + 1 project ≈ 14+).
    result = tools.run_cypher(
        backend, project,
        query="MATCH (n) RETURN n LIMIT 100",
        row_limit=3,
    )
    assert result["truncated"] is True
    assert result["row_count"] == 3


def test_run_cypher_project_auto_injected_against_real_graph(
    ingested_sample,
):
    """A query referencing $project without passing it must scope correctly."""
    backend, project = ingested_sample
    result = tools.run_cypher(
        backend, project,
        query=("MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
               "RETURN f.path AS path"),
    )
    paths = {row["path"] for row in result["rows"]}
    assert paths == {"calculator.py", "runner.py", "test_calculator.py"}
```

- [ ] **Step 3: Run the integration tests**

```bash
.venv/bin/pytest tests/integration/test_cypher_query_integration.py -v -m integration 2>&1 | tail -20
```
Expected: 6 passed.

If `test_run_cypher_read_transaction_blocks_write` fails (engine actually allowed the write), check the `execute_read` implementation — it must use `session(default_access_mode="READ")` AND `session.execute_read(...)`. If `test_run_cypher_timeout_fires` doesn't fire (the query is too fast for even a 0-second timeout), increase the work in the query (more cartesian terms, or a longer variable-length path).

Do NOT weaken assertions. Each test verifies a specific safety guarantee.

- [ ] **Step 4: Run the full integration suite for no regressions**

```bash
.venv/bin/pytest -m integration -q 2>&1 | tail -3
```
Expected: previous integration count + 6 (Phase 5 was at 26, so 32 now), all green.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_cypher_query_integration.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "test: describe_schema and run_cypher integration tests against real Neo4j"
```

## Report
Status, full step 3 output, step 4 totals, commit SHA, any debugging you did.

---

## Task 9: README + final verify

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a new section to `README.md`** (preserve existing content, add at the end):

```markdown

## Free-form queries (`describe_schema` + `run_cypher`)

For questions the 11 structured tools don't cover, livegraph exposes two
"open" MCP tools so the agent can compose Cypher itself:

1. **`describe_schema()`** — returns the graph's labels, edges, properties,
   safety rules, the configured project name, and six example queries.
   Call it once per session.

2. **`run_cypher(query, params?, row_limit?, timeout_seconds?)`** — runs a
   read-only Cypher query. Belt-and-suspenders safety:
   - Lexical pre-scan rejects writes (CREATE, MERGE, DELETE, SET, REMOVE,
     DROP, LOAD CSV, USING PERIODIC COMMIT, CALL) with a friendly error.
   - Neo4j READ transaction enforces read mode at the engine.
   - Per-transaction timeout (default 30s).
   - Server-side row truncation (default 1000 rows) with a `truncated`
     flag on the response.
   - `$project` parameter is auto-injected so queries can use the
     configured project name symbolically.

Example agent prompt: *"Show me functions in this project that are called
by something runtime-only and have no tests."* The agent reads
`describe_schema`, composes the Cypher, calls `run_cypher`, and returns the
answer — all without livegraph needing an LLM dependency of its own.

The agent's LLM (Claude Sonnet, Opus, GPT-5, whichever) writes the Cypher.
livegraph just provides the safe execution endpoint.
```

- [ ] **Step 2: Final full-suite verify**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
.venv/bin/pytest -m integration -q 2>&1 | tail -3
.venv/bin/ruff check livegraph 2>&1 | tail -5
```
Expected: all unit + all integration tests pass; ruff clean. Fix any ruff issues that came from Phase 6 code only (`livegraph/mcp/cypher_guard.py`, `livegraph/mcp/tools.py`, `livegraph/mcp/server.py`, `livegraph/graph/backend.py`, `livegraph/config.py`).

- [ ] **Step 3: Commit**

```bash
git add README.md
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "docs: add describe_schema + run_cypher section to README"
```

## Report
Status, exact unit total, exact integration total, ruff result, commit SHA, any ruff fixes applied.

---

## Done

After Task 9, `livegraph mcp` serves 13 tools. Agents read `describe_schema` once to get the labels, edges, properties, project name, safety rules, and six example queries; then they compose Cypher and submit it through `run_cypher`. The belt-and-suspenders safety (lexical scan + Neo4j read transaction + auto-LIMIT + timeout + truncation) makes free-form queries safe to expose; the example queries teach the agent the project-scoping idiom.

Try it manually after merging:

```bash
LIVEGRAPH_PROJECT=<name> livegraph mcp     # 13 tools now
```

In Claude Code, ask: *"Run a Cypher query to find functions that are
called by something runtime-only and have no tests."* The agent calls
`describe_schema`, composes the Cypher, calls `run_cypher`, and returns
results.

Out of scope (deliberately deferred): procedure allowlist for read-only
`CALL`, a `livegraph query` CLI subcommand, EXPLAIN-plan inspection,
pagination/cursor APIs, write-mode tools. Each remains a candidate for a
future spec.
