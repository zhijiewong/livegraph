# livegraph Phase 4 — `change_impact` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 4 MCP tool: `change_impact(diff, max_depth, provenance, limit)` — turns a unified git-diff string into changed symbols, impacted (transitive) callers with per-edge provenance, and the tests to run.

**Architecture:** A pure-Python `parse_diff` in `livegraph/mcp/diff_parser.py` extracts `{file: set[changed_lines]}` from a unified-diff string. A `change_impact` pure function in `livegraph/mcp/tools.py` calls the parser, then runs three project-scoped Cypher queries (Query A: changed symbols; Query B: impacted callers via `[:CALLS*1..max_depth]`; Query C: covering tests). A thin FastMCP wrapper in `livegraph/mcp/server.py` registers it as the server's 11th tool.

**Tech Stack:** Python 3.12+, no new runtime dependencies. Reuses the existing `mcp>=1.10` SDK, the `neo4j` driver, the Phase 1+2 graph schema, and Phase 3's `pure-function + FastMCP-shim` pattern.

**Reference:** Design spec at `docs/superpowers/specs/2026-05-23-livegraph-phase4-change-impact-design.md`.

**Conventions for every task:**
- Run tests from the repo root: `cd /Users/yvon.zhu/Documents/GitHub/livegraph`.
- Unit tests need no Neo4j. Integration tests are `@pytest.mark.integration` and need Neo4j up (`brew services start neo4j` or `docker compose up -d`).
- If git complains about identity, use `git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit ...`.
- All work happens on a feature branch (`implement-phase-4-change-impact`) created in Task 1.

---

## Task 1: Branch + Phase 4 scaffolding

**Files:**
- No new files (branch only).

- [ ] **Step 1: Create the feature branch**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
git checkout main
git pull --ff-only
git checkout -b implement-phase-4-change-impact
```

- [ ] **Step 2: Sanity-check the existing suite still passes**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: previous test count (~91) passes, no errors. If anything fails, stop and report — main is supposed to be green.

---

## Task 2: Unified-diff parser (`diff_parser.py`)

**Files:**
- Create: `livegraph/mcp/diff_parser.py`
- Test: `tests/unit/test_diff_parser.py`

The parser is the most-tested unit in Phase 4 — it has the most edge cases and zero graph dependencies.

- [ ] **Step 1: Write the failing test** — `tests/unit/test_diff_parser.py`:

```python
from livegraph.mcp.diff_parser import parse_diff


SIMPLE_MODIFY = """\
diff --git a/livegraph/foo.py b/livegraph/foo.py
index abc..def 100644
--- a/livegraph/foo.py
+++ b/livegraph/foo.py
@@ -10,7 +10,9 @@ def existing_thing():
     return 1


-def changed_function():
-    return 2
+def changed_function():
+    return "two"
+
+def new_function():
+    return 3
"""


def test_parses_single_file_modify():
    result = parse_diff(SIMPLE_MODIFY)
    # Walk with current_new_line=10:
    #   10: '     return 1'          context, advance -> 11
    #   11: ''                       blank context, advance -> 12
    #   12: ''                       blank context, advance -> 13
    #   '-def changed_function():'   deletion, no advance (current=13)
    #   '-    return 2'              deletion, no advance (current=13)
    #   '+def changed_function():'   MARK 13, advance -> 14
    #   '+    return "two"'          MARK 14, advance -> 15
    #   '+'                          MARK 15, advance -> 16
    #   '+def new_function():'       MARK 16, advance -> 17
    #   '+    return 3'              MARK 17, advance -> 18
    assert result == {"livegraph/foo.py": {13, 14, 15, 16, 17}}


def test_parses_multiple_files_in_one_diff():
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -5,1 +5,1 @@\n"
        "-y = 1\n"
        "+y = 2\n"
    )
    assert parse_diff(diff) == {"a.py": {1}, "b.py": {5}}


def test_parses_multi_hunk_in_single_file():
    diff = (
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old1\n"
        "+new1\n"
        "@@ -10,1 +10,1 @@\n"
        "-old10\n"
        "+new10\n"
    )
    assert parse_diff(diff) == {"x.py": {1, 10}}


def test_new_file_addition_marks_all_added_lines():
    diff = (
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def f():\n"
        "+    return 1\n"
        "+\n"
    )
    assert parse_diff(diff) == {"new.py": {1, 2, 3}}


def test_deleted_file_is_skipped():
    diff = (
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def gone():\n"
        "-    pass\n"
    )
    assert parse_diff(diff) == {}


def test_binary_diff_produces_no_entry():
    diff = (
        "diff --git a/img.png b/img.png\n"
        "Binary files a/img.png and b/img.png differ\n"
    )
    assert parse_diff(diff) == {}


def test_empty_diff_returns_empty_dict():
    assert parse_diff("") == {}


def test_normalizes_windows_path_separators():
    diff = (
        "--- a/pkg\\sub\\m.py\n"
        "+++ b/pkg\\sub\\m.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    assert parse_diff(diff) == {"pkg/sub/m.py": {1}}


def test_skips_no_newline_at_end_marker():
    diff = (
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "\\ No newline at end of file\n"
        "+y\n"
    )
    # The '\' marker is informational and must not consume a line counter.
    assert parse_diff(diff) == {"a.py": {1}}


def test_garbage_input_is_tolerated():
    # No '+++' headers, no '@@' hunks — should not raise.
    assert parse_diff("complete garbage\nwith no diff headers\n") == {}
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_diff_parser.py -v 2>&1 | tail -5
```
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.mcp.diff_parser'`.

- [ ] **Step 3: Write `livegraph/mcp/diff_parser.py`**

```python
"""Parse unified `git diff` text into ``{file_path: set[new_file_lines]}``.

Pure stdlib. Tolerant: unrecognized lines are skipped, never raise on
malformed input. New files (``--- /dev/null``) are tracked; deleted
files (``+++ /dev/null``) are skipped (a documented v1 limitation).
"""
from __future__ import annotations

import re

# Matches a hunk header like ``@@ -A,B +P,Q @@`` or ``@@ -A +P @@`` (count optional).
_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def parse_diff(diff_text: str) -> dict[str, set[int]]:
    """Return a map from file path to the set of new-file line numbers touched.

    The path is normalized to forward slashes. Files added by the diff are
    included. Files deleted by the diff are not (their ``+++`` is ``/dev/null``).
    """
    result: dict[str, set[int]] = {}
    current_file: str | None = None
    skip_block = False
    current_line: int | None = None  # walking counter inside a hunk

    for line in diff_text.splitlines():
        # New file block — captures the path of the file we're tracking.
        if line.startswith("+++ "):
            target = line[len("+++ "):].strip()
            if target == "/dev/null":
                current_file = None
                skip_block = True
            else:
                # Strip the conventional ``b/`` prefix git uses.
                if target.startswith("b/"):
                    target = target[len("b/"):]
                current_file = target.replace("\\", "/")
                skip_block = False
                result.setdefault(current_file, set())
            current_line = None
            continue

        # ``---`` lines just mark the start of a block; the ``+++`` that
        # follows is what decides whether we record anything.
        if line.startswith("--- "):
            continue

        # Inside a deleted-file block, ignore everything until the next ``+++``.
        if skip_block or current_file is None:
            continue

        # Hunk header sets the new-file line counter.
        hunk = _HUNK_RE.match(line)
        if hunk is not None:
            current_line = int(hunk.group("new_start"))
            continue

        if current_line is None:
            continue

        # The "no newline at end of file" sentinel does not consume a counter.
        if line.startswith("\\"):
            continue

        if line.startswith("+"):
            result[current_file].add(current_line)
            current_line += 1
        elif line.startswith("-"):
            # Deletion: no new-file line consumed.
            continue
        elif line.startswith(" ") or line == "":
            # Context line. Real `git diff` uses ' ' for blank context lines,
            # but some patch tools strip trailing whitespace and produce a
            # truly empty line. Either form is treated as context here.
            current_line += 1
        else:
            # ``diff --git ...``, ``index ...``, anything else outside the
            # body: ignore. Do NOT advance the counter.
            continue

    # Drop files where we recorded nothing (e.g., a file appeared in a header
    # but had only context-only hunks). This keeps callers free of empty sets.
    return {path: lines for path, lines in result.items() if lines}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_diff_parser.py -v 2>&1 | tail -15
```
Expected: 10 passed.

If any test fails, do NOT weaken assertions. The expected outputs were derived by hand-walking the diff format and are correct.

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/diff_parser.py tests/unit/test_diff_parser.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: unified-diff parser for change_impact"
```

---

## Task 3: `change_impact` tool function

**Files:**
- Modify: `livegraph/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools_change_impact.py`

The tool orchestrates `parse_diff` plus three Cypher queries. Unit tests use a small queued-response backend defined inline in the test file (the shared `FakeBackend` returns the same rows for every call, which doesn't fit a tool that runs three different queries).

- [ ] **Step 1: Write the failing test** — `tests/unit/test_mcp_tools_change_impact.py`:

```python
from typing import Any

from livegraph.mcp.tools import change_impact


class _QueuedBackend:
    """Test backend that returns a different canned response per execute call."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def verify(self) -> None:
        return None

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        if not self._responses:
            return []
        return self._responses.pop(0)

    def close(self) -> None:
        return None


_SIMPLE_DIFF = (
    "--- a/calculator.py\n"
    "+++ b/calculator.py\n"
    "@@ -5,3 +5,3 @@ class Calculator:\n"
    "     def add(self, a, b):\n"
    "-        return a + b\n"
    "+        return a + b + 0\n"
)


def test_change_impact_assembles_changed_impacted_tests():
    backend = _QueuedBackend([
        # Query A — changed symbols
        [{
            "qualified_name": "calculator.py::Calculator.add",
            "name": "add", "kind": "method", "file": "calculator.py",
            "start_line": 5, "end_line": 7,
            "runtime_observed": True, "coverage_pct": 100.0,
        }],
        # Query B — impacted callers
        [{
            "qualified_name": "runner.py::run_operation",
            "name": "run_operation", "kind": "function",
            "file": "runner.py", "start_line": 7, "end_line": 8,
            "runtime_observed": True, "coverage_pct": 100.0,
            "reached_via": [{
                "via": "calculator.py::Calculator.add",
                "depth": 1,
                "edges": [{"static": False, "runtime": True}],
            }],
        }],
        # Query C — tests
        [{
            "qualified_name": "test_calculator.py::test_main", "name": "test_main",
            "kind": "function", "file": "test_calculator.py",
            "start_line": 10, "end_line": 12,
            "test_outcome": "passed",
            "covers_symbols": [
                "calculator.py::Calculator.add", "runner.py::run_operation",
            ],
            "avg_coverage_pct": 100.0,
        }],
    ])

    result = change_impact(backend, project="sample", diff=_SIMPLE_DIFF)

    assert result["changed"][0]["qualified_name"] == "calculator.py::Calculator.add"
    assert result["impacted"][0]["qualified_name"] == "runner.py::run_operation"
    assert result["impacted"][0]["reached_via"][0]["depth"] == 1
    assert result["impacted"][0]["reached_via"][0]["edges"][0]["runtime"] is True
    assert result["tests_to_run"][0]["qualified_name"] == "test_calculator.py::test_main"
    assert result["unmatched_files"] == []
    assert result["stats"] == {
        "changed_files": 1,
        "changed_symbols": 1,
        "impacted_symbols": 1,
        "tests_to_run": 1,
        "max_depth_reached": 1,
    }


def test_change_impact_reports_unmatched_files_when_query_a_returns_nothing():
    # Query A returns []: file in the diff is not in the graph.
    backend = _QueuedBackend([[], [], []])
    result = change_impact(backend, project="p", diff=_SIMPLE_DIFF)
    assert result["unmatched_files"] == ["calculator.py"]
    assert result["changed"] == []
    assert result["impacted"] == []
    assert result["tests_to_run"] == []
    assert result["stats"]["changed_files"] == 1
    assert result["stats"]["changed_symbols"] == 0


def test_change_impact_clamps_max_depth_to_range():
    backend = _QueuedBackend([[], [], []])
    # max_depth above the cap is silently clamped down to 20.
    change_impact(backend, project="p", diff=_SIMPLE_DIFF, max_depth=999)
    query_b = backend.calls[1][0]
    assert "CALLS*1..20" in query_b

    backend = _QueuedBackend([[], [], []])
    # And clamped up to 1.
    change_impact(backend, project="p", diff=_SIMPLE_DIFF, max_depth=0)
    query_b = backend.calls[1][0]
    assert "CALLS*1..1" in query_b


def test_change_impact_passes_provenance_filter():
    backend = _QueuedBackend([[], [], []])
    change_impact(backend, project="p", diff=_SIMPLE_DIFF, provenance="runtime")
    _q, params = backend.calls[1]
    assert params["provenance"] == "runtime"


def test_change_impact_passes_limit_to_impacted_query():
    backend = _QueuedBackend([[], [], []])
    change_impact(backend, project="p", diff=_SIMPLE_DIFF, limit=7)
    _q, params = backend.calls[1]
    assert params["limit"] == 7


def test_change_impact_with_empty_diff_returns_all_empty():
    backend = _QueuedBackend([])
    result = change_impact(backend, project="p", diff="")
    assert result["changed"] == []
    assert result["impacted"] == []
    assert result["tests_to_run"] == []
    assert result["unmatched_files"] == []
    assert result["stats"]["changed_files"] == 0
    # No Cypher should have been issued.
    assert backend.calls == []
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_change_impact.py -v 2>&1 | tail -10
```
Expected: FAIL — `ImportError: cannot import name 'change_impact' from 'livegraph.mcp.tools'`.

- [ ] **Step 3: Append to `livegraph/mcp/tools.py`**

Add this import near the top of `livegraph/mcp/tools.py`:

```python
from livegraph.mcp.diff_parser import parse_diff
```

Append the following at the end of `livegraph/mcp/tools.py`:

```python
# -- change_impact ----------------------------------------------------

_MAX_DEPTH_MIN = 1
_MAX_DEPTH_MAX = 20

# Query A — changed symbols
_CHANGE_IMPACT_QUERY_A = (
    "UNWIND $files AS spec "
    "MATCH (:Project {name: $project})-[:CONTAINS]->(file:File "
    "    {path: spec.path}) "
    "MATCH (file)-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE (s:Function OR s:Method) "
    "  AND any(line IN spec.lines WHERE "
    "          line >= s.start_line AND line <= s.end_line) "
    "RETURN DISTINCT s.qualified_name AS qualified_name, s.name AS name, "
    "       head([l IN labels(s) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       s.file AS file, s.start_line AS start_line, "
    "       s.end_line AS end_line, "
    "       coalesce(s.runtime_observed, false) AS runtime_observed, "
    "       coalesce(s.coverage_pct, 0.0) AS coverage_pct"
)

# Query C — tests for any (changed ∪ impacted) symbol
_CHANGE_IMPACT_QUERY_C = (
    "UNWIND $all_affected_qns AS qn "
    "MATCH (s {qualified_name: qn}) "
    "MATCH (t:Test)-[c:COVERS]->(s) "
    "RETURN DISTINCT t.qualified_name AS qualified_name, t.name AS name, "
    "       head([l IN labels(t) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       t.file AS file, t.start_line AS start_line, "
    "       t.end_line AS end_line, "
    "       coalesce(t.test_outcome, '') AS test_outcome, "
    "       collect(DISTINCT qn) AS covers_symbols, "
    "       avg(coalesce(c.coverage_pct, 0.0)) AS avg_coverage_pct "
    "ORDER BY t.qualified_name"
)


def _query_b_cypher(max_depth: int) -> str:
    """Build the impacted-callers query with a safely-interpolated depth."""
    return (
        "UNWIND $changed_qns AS changed_qn "
        "MATCH (changed {qualified_name: changed_qn}) "
        "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
        "-[:DEFINES|HAS_METHOD*1..2]->(impacted) "
        f"MATCH path = (impacted)-[:CALLS*1..{max_depth}]->(changed) "
        "WHERE all(rel IN relationships(path) WHERE "
        "          ($provenance = 'any') "
        "       OR ($provenance = 'static'  AND rel.static  = true) "
        "       OR ($provenance = 'runtime' AND rel.runtime = true)) "
        "WITH impacted, changed_qn, length(path) AS depth, "
        "     [r IN relationships(path) | "
        "       {static: coalesce(r.static, false), "
        "        runtime: coalesce(r.runtime, false)}] AS edge_provenance "
        "RETURN impacted.qualified_name AS qualified_name, "
        "       impacted.name AS name, "
        "       head([l IN labels(impacted) "
        "             WHERE l IN ['Function','Method','Class'] "
        "             | toLower(l)]) AS kind, "
        "       impacted.file AS file, "
        "       impacted.start_line AS start_line, "
        "       impacted.end_line AS end_line, "
        "       coalesce(impacted.runtime_observed, false) "
        "         AS runtime_observed, "
        "       coalesce(impacted.coverage_pct, 0.0) AS coverage_pct, "
        "       collect(DISTINCT {via: changed_qn, depth: depth, "
        "                         edges: edge_provenance}) AS reached_via "
        "ORDER BY qualified_name "
        "LIMIT $limit"
    )


def change_impact(
    backend: GraphBackend, project: str, diff: str,
    max_depth: int = 5, provenance: str = "any", limit: int = 200,
) -> dict[str, Any]:
    """Given a unified diff, return changed/impacted symbols and tests to run.

    Returns a dict with ``changed``, ``impacted``, ``tests_to_run``,
    ``unmatched_files``, and ``stats`` keys. See the design spec for the
    full schema.
    """
    # Clamp max_depth to a sane, Cypher-safe range. provenance is checked
    # here because the value is interpolated into a CASE expression; only
    # known string values reach the query.
    max_depth = max(_MAX_DEPTH_MIN, min(_MAX_DEPTH_MAX, int(max_depth)))
    if provenance not in ("any", "static", "runtime"):
        provenance = "any"

    parsed = parse_diff(diff)
    if not parsed:
        return _empty_result(changed_files=0)

    files_spec = [
        {"path": path, "lines": sorted(lines)}
        for path, lines in sorted(parsed.items())
    ]

    # Query A
    changed_rows = backend.execute(
        _CHANGE_IMPACT_QUERY_A, project=project, files=files_spec,
    )
    changed = [_change_symbol_from_row(r) for r in changed_rows]
    changed_qns = [c["qualified_name"] for c in changed]
    files_in_changed = {c["file"] for c in changed}
    unmatched_files = sorted(set(parsed.keys()) - files_in_changed)

    # Query B
    impacted_rows: list[dict[str, Any]] = []
    if changed_qns:
        impacted_rows = backend.execute(
            _query_b_cypher(max_depth),
            project=project, changed_qns=changed_qns,
            provenance=provenance, limit=limit,
        )
    impacted = [_impacted_from_row(r) for r in impacted_rows]

    # Query C
    all_affected_qns = sorted({
        *changed_qns,
        *(i["qualified_name"] for i in impacted),
    })
    test_rows: list[dict[str, Any]] = []
    if all_affected_qns:
        test_rows = backend.execute(
            _CHANGE_IMPACT_QUERY_C, project=project,
            all_affected_qns=all_affected_qns,
        )
    tests_to_run = [_test_from_row(r) for r in test_rows]

    max_depth_reached = max(
        (
            via["depth"]
            for sym in impacted
            for via in sym["reached_via"]
        ),
        default=0,
    )

    return {
        "changed": changed,
        "impacted": impacted,
        "tests_to_run": tests_to_run,
        "unmatched_files": unmatched_files,
        "stats": {
            "changed_files": len(parsed),
            "changed_symbols": len(changed),
            "impacted_symbols": len(impacted),
            "tests_to_run": len(tests_to_run),
            "max_depth_reached": max_depth_reached,
        },
    }


def _empty_result(changed_files: int) -> dict[str, Any]:
    return {
        "changed": [],
        "impacted": [],
        "tests_to_run": [],
        "unmatched_files": [],
        "stats": {
            "changed_files": changed_files,
            "changed_symbols": 0,
            "impacted_symbols": 0,
            "tests_to_run": 0,
            "max_depth_reached": 0,
        },
    }


def _change_symbol_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_symbol_from_row(row),
        "runtime_observed": bool(row.get("runtime_observed")),
        "coverage_pct": float(row.get("coverage_pct") or 0.0),
    }


def _impacted_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_symbol_from_row(row),
        "runtime_observed": bool(row.get("runtime_observed")),
        "coverage_pct": float(row.get("coverage_pct") or 0.0),
        "reached_via": [
            {
                "via": entry["via"],
                "depth": int(entry["depth"]),
                "edges": [
                    {"static": bool(e.get("static")),
                     "runtime": bool(e.get("runtime"))}
                    for e in entry.get("edges") or []
                ],
            }
            for entry in row.get("reached_via") or []
        ],
    }


def _test_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_symbol_from_row(row),
        "test_outcome": row.get("test_outcome") or "",
        "covers_symbols": sorted(row.get("covers_symbols") or []),
        "avg_coverage_pct": float(row.get("avg_coverage_pct") or 0.0),
    }
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_change_impact.py -v 2>&1 | tail -10
```
Expected: 6 passed.

- [ ] **Step 5: Confirm full unit suite still passes**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: previous total + 16 (10 parser + 6 tool) tests, all green.

- [ ] **Step 6: Commit**

```bash
git add livegraph/mcp/tools.py tests/unit/test_mcp_tools_change_impact.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: change_impact tool function (Phase 4 core)"
```

---

## Task 4: MCP server wiring

**Files:**
- Modify: `livegraph/mcp/server.py`
- Test: `tests/unit/test_mcp_server.py`

Register `change_impact` as the 11th FastMCP tool.

- [ ] **Step 1: Append the failing test to `tests/unit/test_mcp_server.py`**

Add this test to the existing file (it currently asserts there are 10 tools — we'll update that count):

```python
def test_build_server_registers_eleven_tools_including_change_impact():
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
    ])
    assert tool_names == expected
```

Also **update the existing `test_build_server_registers_all_ten_tools` test** — rename it and update the expected list to include `change_impact`. Since it's now redundant with the new test, *delete* the old test entirely and keep only the new 11-tool test.

- [ ] **Step 2: Run failing test**

```bash
.venv/bin/pytest tests/unit/test_mcp_server.py -v 2>&1 | tail -10
```
Expected: 1 failure — `change_impact` not in the registered tool list.

- [ ] **Step 3: Add the wrapper to `livegraph/mcp/server.py`**

Inside the `build_server()` function, after the `graph_status` tool registration (last one currently) and before the `return mcp` line, add:

```python
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
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/bin/pytest tests/unit/test_mcp_server.py -v 2>&1 | tail -10
```
Expected: all tests pass; tool count is 11.

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/server.py tests/unit/test_mcp_server.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: register change_impact as the 11th MCP tool"
```

---

## Task 5: Integration test against real Neo4j

**Files:**
- Create: `tests/integration/test_change_impact_integration.py`

End-to-end Cypher verification. Reuses Phase 3's `ingested_sample` fixture (Phase 1 + Phase 2 already run on the sample project).

- [ ] **Step 1: Write the integration tests**

```python
# tests/integration/test_change_impact_integration.py
"""End-to-end Cypher tests for the change_impact MCP tool."""
import pytest

from livegraph.mcp import tools

pytestmark = pytest.mark.integration


# A real-ish diff that touches Calculator.add. The hunk numbers are chosen
# so the new-file lines (8, 9) overlap the symbol's [start_line=8, end_line=9]
# span as written in tests/fixtures/sample_project/calculator.py:
#     class Calculator:
#         def add(self, a, b):
#             return a + b
_DIFF_TOUCHING_ADD = (
    "diff --git a/calculator.py b/calculator.py\n"
    "--- a/calculator.py\n"
    "+++ b/calculator.py\n"
    "@@ -7,3 +7,3 @@ class Calculator:\n"
    "     def add(self, a, b):\n"
    "-        return a + b\n"
    "+        return a + b + 0\n"
)


def test_change_impact_finds_runtime_only_dynamic_dispatch_caller(
    ingested_sample,
):
    """The Phase 4 acceptance test.

    A diff that changes Calculator.add must impact runner.py::run_operation
    via a runtime-observed edge (the dynamic dispatch op(a, b) that no
    purely-static tool can resolve).
    """
    backend, project = ingested_sample
    result = tools.change_impact(backend, project, diff=_DIFF_TOUCHING_ADD)

    changed_qns = {c["qualified_name"] for c in result["changed"]}
    assert "calculator.py::Calculator.add" in changed_qns

    impacted_qns = {i["qualified_name"] for i in result["impacted"]}
    assert "runner.py::run_operation" in impacted_qns

    # Verify the run_operation impact path has a runtime edge.
    run_op = next(
        i for i in result["impacted"]
        if i["qualified_name"] == "runner.py::run_operation"
    )
    assert any(
        any(edge["runtime"] for edge in entry["edges"])
        for entry in run_op["reached_via"]
    )
    # Minimum-depth chain should be 1 (direct caller).
    assert min(entry["depth"] for entry in run_op["reached_via"]) == 1


def test_change_impact_returns_tests_to_run(ingested_sample):
    backend, project = ingested_sample
    result = tools.change_impact(backend, project, diff=_DIFF_TOUCHING_ADD)
    test_qns = {t["qualified_name"] for t in result["tests_to_run"]}
    # At least one of the fixture's tests must cover Calculator.add or its
    # transitive caller, and so must surface here.
    assert test_qns, f"expected at least one test, got {result['tests_to_run']!r}"


def test_change_impact_unmatched_files_for_unknown_path(ingested_sample):
    backend, project = ingested_sample
    diff = (
        "diff --git a/never_ingested.py b/never_ingested.py\n"
        "--- a/never_ingested.py\n"
        "+++ b/never_ingested.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    result = tools.change_impact(backend, project, diff=diff)
    assert result["unmatched_files"] == ["never_ingested.py"]
    assert result["changed"] == []
    assert result["impacted"] == []
    assert result["tests_to_run"] == []
    assert result["stats"]["changed_files"] == 1
    assert result["stats"]["changed_symbols"] == 0
```

- [ ] **Step 2: Verify Neo4j is up**

```bash
(echo > /dev/tcp/localhost/7687) 2>/dev/null && echo "neo4j up" || echo "neo4j DOWN — start with: brew services start neo4j"
```

If Neo4j is down, start it (`brew services start neo4j`) and wait for `localhost:7687`.

- [ ] **Step 3: Run the integration tests**

```bash
.venv/bin/pytest tests/integration/test_change_impact_integration.py -v -m integration 2>&1 | tail -15
```
Expected: 3 passed.

If `test_change_impact_finds_runtime_only_dynamic_dispatch_caller` fails:
- **Check the diff hunk line numbers.** Open `tests/fixtures/sample_project/calculator.py` and find the actual line range of the `add` method. Adjust the `@@ -A,B +P,Q @@` header in `_DIFF_TOUCHING_ADD` so the `+` lines fall inside that range. The body of `add` is `return a + b`; the diff just needs to touch a line inside `add`.
- **Verify the runtime edge exists.** In Neo4j Browser: `MATCH (caller {qualified_name: "runner.py::run_operation"})-[c:CALLS]->(callee {qualified_name: "calculator.py::Calculator.add"}) RETURN c.runtime` should return `true`.

Do NOT weaken assertions in the test — fix the diff to actually touch `add`'s lines.

- [ ] **Step 4: Run the full integration suite**

```bash
.venv/bin/pytest -m integration -q 2>&1 | tail -3
```
Expected: previous integration count + 3.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_change_impact_integration.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "test: change_impact integration tests against real Neo4j"
```

---

## Task 6: README update + final verify

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the tool table in `README.md`**

Find the existing MCP tool table (the 10-row table from the Phase 3 README section). Add one new row at the bottom, before the closing of the table:

```markdown
| **`change_impact(diff, max_depth, provenance, limit)`** | Given a git diff: changed symbols, transitive callers with per-edge provenance, and the tests to run |
```

(The bold formatting on `change_impact` matches how `runtime_only_calls` is bolded — both are the differentiator tools that no static-only tool can implement.)

- [ ] **Step 2: Final full-suite verify**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
.venv/bin/pytest -m integration -q 2>&1 | tail -3
.venv/bin/ruff check livegraph 2>&1 | tail -3
```
Expected: all unit + all integration tests pass; ruff clean. Fix any ruff issues introduced by Phase 4 code (do not refactor anything outside `livegraph/mcp/`).

- [ ] **Step 3: Commit**

```bash
git add README.md
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "docs: add change_impact to MCP tool table"
```

---

## Done

After Task 6, `livegraph mcp` exposes 11 tools. An agent can hand it a unified diff and get back the symbols changed, the transitive callers impacted (with per-edge static/runtime provenance), the tests to run, and per-symbol coverage stats. The Phase 4 acceptance test in Task 5 proves the differentiator: a diff of `Calculator.add` surfaces `runner.py::run_operation` as impacted via a runtime-observed edge — the dynamic-dispatch caller no static-only blast-radius tool can find.

Try it manually after merging:

```bash
livegraph build /path/to/some/python/project    # Phase 1 + 2 (re-run if not ingested)
LIVEGRAPH_PROJECT=<name> livegraph mcp           # Phase 3 server (now with change_impact)

# In your MCP host, ask the agent:
#   "Here's the diff from my current branch:
#    <paste output of `git diff main HEAD`>
#    What will be affected and which tests should I run?"
# A working integration finds and calls change_impact.
```

Out of scope (as designed): risk scoring, rename/delete impact, NL→Cypher, embeddings, multi-language, incremental updates, a `livegraph impact` CLI. Each remains a candidate for a future spec.
