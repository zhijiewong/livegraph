# livegraph Phase 9 — `semantic_neighborhood` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 15th MCP tool `semantic_neighborhood(query, ...)` that fuses Phase 7 vector search with Phase 1/2 callgraph + coverage: for each semantic seed, return its direct callers, callees, and tests in one call.

**Architecture:** Extract the seed step from `semantic_search` into a shared helper, then layer three batched `UNWIND $qns`-style expansion queries on top. Lives in a new file `livegraph/mcp/tools_neighborhood.py` to keep `tools.py` from growing further. Registered as the 15th `@mcp.tool()` in `server.py`.

**Tech Stack:** Python 3.12+, existing Neo4j driver, existing FastMCP (`mcp>=1.10`), existing `EmbeddingProvider` Protocol (Phase 7).

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `livegraph/mcp/tools.py` | Modify | Extract `_semantic_seeds(...)` helper; `semantic_search` calls into it. |
| `livegraph/mcp/tools_neighborhood.py` | Create | `semantic_neighborhood(...)` + 3 expansion Cypher queries + Python-side join. |
| `livegraph/mcp/server.py` | Modify | Register `semantic_neighborhood` as the 15th tool. Update "14 tools" → "15 tools" in the docstring. |
| `tests/unit/test_mcp_tools_semantic_neighborhood.py` | Create | 8 behavior tests (param clamping, kind validation, missing extra, expansion grouping, include filter, provenance collapse, min_score). |
| `tests/integration/test_semantic_neighborhood_integration.py` | Create | 2 tests against real Neo4j + real MiniLM. |
| `tests/integration/test_mcp_server_smoke.py` | Modify | Add `"semantic_neighborhood"` to the sorted assertion. |
| `README.md` | Modify | Add "Semantic neighborhood" section. |

---

## Task 1: Extract `_semantic_seeds` helper

**Files:**
- Modify: `livegraph/mcp/tools.py` (around lines 967–1020)
- Test: `tests/unit/test_mcp_tools_semantic_search.py` (run existing tests after the change to verify no regression)

The current `semantic_search` does kind-validation → index-existence check → encode → vector-query → assemble rows. The seed-finding portion (kind validation through to a list of seed dicts, plus the warning/empty-result shape) is what we need to share. Extract it as `_semantic_seeds(backend, project, provider, query, limit, kind, min_score=0.0)` returning either:

```python
{"ok": True, "seeds": [{...row from vector query...}, ...]}
```

or, when the seed step shouldn't proceed (invalid kind, no index, missing extra etc.):

```python
{"ok": False, "warning": "...", "embedded_count": 0}
```

This keeps `semantic_search`'s and `semantic_neighborhood`'s graceful-degradation surfaces identical.

- [ ] **Step 1: Read the current `semantic_search`**

Open `/Users/yvon.zhu/Documents/GitHub/livegraph/livegraph/mcp/tools.py` around lines 967–1020. The function:

```python
def semantic_search(
    backend: GraphBackend, project: str, provider: EmbeddingProvider,
    query: str, limit: int = 10, kind: str = "any",
) -> dict[str, Any]:
    if kind not in ("any", "function", "method"):
        return {"results": [], "model": provider.name, "embedded_count": 0,
                "warning": f"invalid kind {kind!r}; ..."}
    if not _index_exists(backend):
        return {"results": [], "model": provider.name, "embedded_count": 0,
                "warning": "no embeddings yet; run `livegraph embed` first"}

    query_vector = provider.encode([query])[0]
    k_padded = limit + 50

    rows = backend.execute(_VECTOR_QUERY_CYPHER, index_name=INDEX_NAME,
        project=project, k_padded=k_padded, query_vector=query_vector,
        kind=kind, limit=limit)

    results = [...]
    return {"results": results, "model": provider.name,
            "embedded_count": _embedded_count(backend, project),
            "warning": None}
```

- [ ] **Step 2: Add the helper just above `semantic_search`**

```python
def _semantic_seeds(
    backend: GraphBackend, project: str, provider: EmbeddingProvider,
    query: str, limit: int, kind: str, min_score: float = 0.0,
) -> dict[str, Any]:
    """Run the kind-check + index-check + vector query.

    Returns either {ok: True, seeds: [row, ...]} or
    {ok: False, warning: str}. Used by both `semantic_search` (which
    builds snippet-bearing results from the seeds) and
    `semantic_neighborhood` (which expands each seed via the callgraph).
    """
    if kind not in ("any", "function", "method"):
        return {
            "ok": False,
            "warning": (
                f"invalid kind {kind!r}; "
                f"must be one of 'any', 'function', 'method'"
            ),
        }
    if not _index_exists(backend):
        return {
            "ok": False,
            "warning": "no embeddings yet; run `livegraph embed` first",
        }
    query_vector = provider.encode([query])[0]
    k_padded = limit + 50
    rows = backend.execute(
        _VECTOR_QUERY_CYPHER,
        index_name=INDEX_NAME, project=project,
        k_padded=k_padded, query_vector=query_vector,
        kind=kind, limit=limit,
    )
    seeds = [r for r in rows if r.get("qualified_name") is not None]
    if min_score > 0.0:
        seeds = [s for s in seeds if float(s.get("score") or 0.0) >= min_score]
    return {"ok": True, "seeds": seeds}
```

- [ ] **Step 3: Replace the body of `semantic_search` to call the helper**

```python
def semantic_search(
    backend: GraphBackend, project: str, provider: EmbeddingProvider,
    query: str, limit: int = 10, kind: str = "any",
) -> dict[str, Any]:
    """Find code symbols by vector similarity to ``query``."""
    seed_result = _semantic_seeds(
        backend, project, provider, query, limit, kind,
    )
    if not seed_result["ok"]:
        return {
            "results": [],
            "model": provider.name,
            "embedded_count": 0,
            "warning": seed_result["warning"],
        }
    results = [
        {
            "qualified_name": r.get("qualified_name"),
            "name": r.get("name"),
            "kind": r.get("kind"),
            "file": r.get("file"),
            "start_line": r.get("start_line"),
            "end_line": r.get("end_line"),
            "score": float(r.get("score") or 0.0),
            "snippet": _snippet(r.get("source") or ""),
        }
        for r in seed_result["seeds"]
    ]
    return {
        "results": results,
        "model": provider.name,
        "embedded_count": _embedded_count(backend, project),
        "warning": None,
    }
```

- [ ] **Step 4: Run the existing `semantic_search` tests to verify no regression**

```
.venv/bin/python -m pytest tests/unit/test_mcp_tools_semantic_search.py -v
```

Expected: all PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools.py
git commit -m "refactor(phase9): extract _semantic_seeds helper from semantic_search"
```

---

## Task 2: Cypher expansion constants

**Files:**
- Create (start of file): `livegraph/mcp/tools_neighborhood.py`

- [ ] **Step 1: Create the new file with the three Cypher queries**

```python
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
```

- [ ] **Step 2: Smoke-import**

```
.venv/bin/python -c "import livegraph.mcp.tools_neighborhood as t; print('ok', t._MAX_LIMIT)"
```
Expected: `ok 50`.

- [ ] **Step 3: Commit**

```bash
git add livegraph/mcp/tools_neighborhood.py
git commit -m "feat(phase9): tools_neighborhood module skeleton + expansion Cypher"
```

---

## Task 3: `semantic_neighborhood` core + unit tests

**Files:**
- Modify: `livegraph/mcp/tools_neighborhood.py`
- Test: `tests/unit/test_mcp_tools_semantic_neighborhood.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mcp_tools_semantic_neighborhood.py`:

```python
from __future__ import annotations

from typing import Any

import pytest

from livegraph.mcp.tools_neighborhood import semantic_neighborhood


class _FakeBackend:
    """Returns canned responses; matches the pattern used by other unit tests."""

    def __init__(self, responses: dict[str, list[dict[str, Any]]]):
        # `responses` maps a substring of the Cypher to its row list.
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        for key, rows in self._responses.items():
            if key in cypher:
                return rows
        return []

    def verify(self): return None
    def close(self): return None


class _FakeProvider:
    name = "fake-model"
    dimensions = 4
    batch_size = 8

    def encode(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def _index_exists_rows():
    return [{"name": "livegraph_symbol_embeddings"}]


def _seed_rows():
    return [
        {"qualified_name": "pkg.A.foo", "name": "foo", "kind": "method",
         "file": "pkg/a.py", "start_line": 1, "end_line": 3,
         "source": "def foo():\n    pass\n", "score": 0.9},
        {"qualified_name": "pkg.B.bar", "name": "bar", "kind": "method",
         "file": "pkg/b.py", "start_line": 1, "end_line": 3,
         "source": "def bar():\n    pass\n", "score": 0.7},
    ]


def test_missing_index_returns_warning_no_calls_beyond_index_check():
    backend = _FakeBackend({"SHOW INDEXES": []})  # no index
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="anything",
    )
    assert result["results"] == []
    assert "embed" in result["warning"]
    # The expansion queries must NOT have been hit.
    assert all("CALLS" not in c[0] for c in backend.calls)


def test_invalid_kind_returns_warning_no_db_calls():
    backend = _FakeBackend({"SHOW INDEXES": _index_exists_rows()})
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", kind="nonsense",
    )
    assert result["results"] == []
    assert "kind" in result["warning"].lower()


def test_default_include_returns_callers_callees_tests_grouped_per_seed():
    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
        "db.index.vector.queryNodes": _seed_rows(),
        # Callers expansion
        "MATCH (caller)-[r:CALLS]->(target)": [
            {"seed_qn": "pkg.A.foo",
             "qualified_name": "pkg.api.handle_foo",
             "provenance": "runtime"},
        ],
        # Callees expansion
        "MATCH (target)-[r:CALLS]->(callee)": [
            {"seed_qn": "pkg.A.foo",
             "qualified_name": "builtins.int",
             "provenance": "static"},
        ],
        # Tests
        "(test:Test)-[:COVERS]->(target)": [
            {"seed_qn": "pkg.A.foo",
             "qualified_name": "tests.test_foo.test_basic"},
        ],
        "RETURN count": [{"n": 50}],
    })
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", limit=2,
    )
    assert result["warning"] is None
    assert len(result["results"]) == 2
    foo = result["results"][0]
    assert foo["qualified_name"] == "pkg.A.foo"
    assert foo["callers"] == [
        {"qualified_name": "pkg.api.handle_foo", "provenance": "runtime"},
    ]
    assert foo["callees"] == [
        {"qualified_name": "builtins.int", "provenance": "static"},
    ]
    assert foo["tests"] == [
        {"qualified_name": "tests.test_foo.test_basic"},
    ]
    # The second seed has no expansion rows; its lists should be empty.
    bar = result["results"][1]
    assert bar["callers"] == []
    assert bar["callees"] == []
    assert bar["tests"] == []


def test_include_subset_skips_other_expansion_queries():
    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
        "db.index.vector.queryNodes": _seed_rows(),
        "MATCH (caller)-[r:CALLS]->(target)": [],
        "RETURN count": [{"n": 0}],
    })
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", include=["callers"],
    )
    cyphers = [c[0] for c in backend.calls]
    assert any("MATCH (caller)-[r:CALLS]->(target)" in c for c in cyphers)
    assert not any("MATCH (target)-[r:CALLS]->(callee)" in c for c in cyphers)
    assert not any("(test:Test)-[:COVERS]->(target)" in c for c in cyphers)
    for r in result["results"]:
        assert "callers" in r
        assert "callees" not in r
        assert "tests" not in r


def test_limit_and_per_seed_limit_are_clamped():
    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
        "db.index.vector.queryNodes": _seed_rows(),
        "MATCH (caller)-[r:CALLS]->(target)": [],
        "MATCH (target)-[r:CALLS]->(callee)": [],
        "(test:Test)-[:COVERS]->(target)": [],
        "RETURN count": [{"n": 0}],
    })
    semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", limit=999, per_seed_limit=999,
    )
    # The vector query should have been called with limit=50 (clamp).
    vec_call = [c for c in backend.calls
                if "db.index.vector.queryNodes" in c[0]][0]
    assert vec_call[1]["limit"] == 50
    # Each expansion call should carry per_seed_limit=50.
    exp_calls = [c for c in backend.calls if "$per_seed_limit" in c[0]]
    assert exp_calls and all(c[1]["per_seed_limit"] == 50 for c in exp_calls)


def test_min_score_filters_seeds():
    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
        "db.index.vector.queryNodes": _seed_rows(),
        "MATCH (caller)-[r:CALLS]->(target)": [],
        "MATCH (target)-[r:CALLS]->(callee)": [],
        "(test:Test)-[:COVERS]->(target)": [],
        "RETURN count": [{"n": 0}],
    })
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", min_score=0.8,
    )
    # Only pkg.A.foo (score 0.9) survives.
    assert [r["qualified_name"] for r in result["results"]] == ["pkg.A.foo"]


def test_provenance_collapses_to_both_when_static_and_runtime():
    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
        "db.index.vector.queryNodes": [_seed_rows()[0]],
        # The Cypher returns provenance="both" — we just verify the tool
        # forwards it unchanged.
        "MATCH (caller)-[r:CALLS]->(target)": [
            {"seed_qn": "pkg.A.foo",
             "qualified_name": "pkg.api.handle_foo",
             "provenance": "both"},
        ],
        "MATCH (target)-[r:CALLS]->(callee)": [],
        "(test:Test)-[:COVERS]->(target)": [],
        "RETURN count": [{"n": 1}],
    })
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", limit=1, include=["callers"],
    )
    assert result["results"][0]["callers"] == [
        {"qualified_name": "pkg.api.handle_foo", "provenance": "both"},
    ]


def test_missing_semantic_extra_returns_install_hint():
    """If the provider's encode raises EmbeddingExtraMissing,
    semantic_neighborhood degrades gracefully like semantic_search does."""
    from livegraph.semantic.provider import EmbeddingExtraMissing

    class _BoomProvider:
        name = "boom"
        dimensions = 4
        batch_size = 1

        def encode(self, texts):
            raise EmbeddingExtraMissing("install livegraph[semantic]")

    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
    })
    result = semantic_neighborhood(
        backend, project="p", provider=_BoomProvider(), query="q",
    )
    assert result["results"] == []
    assert "semantic" in result["warning"].lower()
```

- [ ] **Step 2: Run the tests — expect collection or assertion failures**

```
.venv/bin/python -m pytest tests/unit/test_mcp_tools_semantic_neighborhood.py -v
```

Expected: collection error (no `semantic_neighborhood` symbol yet).

- [ ] **Step 3: Implement `semantic_neighborhood`**

Append to `livegraph/mcp/tools_neighborhood.py`:

```python
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
```

- [ ] **Step 4: Run the tests — expect all 8 PASS**

```
.venv/bin/python -m pytest tests/unit/test_mcp_tools_semantic_neighborhood.py -v
```

- [ ] **Step 5: Run the full unit suite to catch regressions**

```
.venv/bin/python -m pytest tests/unit/ -q
```

Expected: all PASS (no Phase 7/8 regressions).

- [ ] **Step 6: Commit**

```bash
git add livegraph/mcp/tools_neighborhood.py tests/unit/test_mcp_tools_semantic_neighborhood.py
git commit -m "feat(phase9): semantic_neighborhood tool + unit tests"
```

---

## Task 4: Register `semantic_neighborhood` as the 15th MCP tool

**Files:**
- Modify: `livegraph/mcp/server.py`

- [ ] **Step 1: Inspect current tool registration**

Open `livegraph/mcp/server.py` and find:
- The list of `@mcp.tool()` registrations.
- The docstring mentioning "14 tools".
- The `_get_or_load_provider()` helper used by `semantic_search`.

- [ ] **Step 2: Add the import**

Near the top of `livegraph/mcp/server.py`, alongside the existing `from livegraph.mcp.tools import ...`, add:

```python
from livegraph.mcp.tools_neighborhood import semantic_neighborhood as _semantic_neighborhood
```

- [ ] **Step 3: Register the tool**

Below the existing `@mcp.tool() def semantic_search(...)` registration in `bootstrap()`, add:

```python
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
        callers, callees, and tests attached to that symbol. Use this
        when you want "where do I look, what do I run" rather than just
        "what matches."
        """
        provider = _get_or_load_provider()
        if provider is None:
            return {
                "results": [], "model": None, "embedded_count": 0,
                "warning": (
                    "semantic search unavailable: install with "
                    "`pip install 'livegraph[semantic]'`"
                ),
            }
        return _semantic_neighborhood(
            backend, project, provider, query=query, limit=limit,
            per_seed_limit=per_seed_limit, kind=kind,
            include=include, min_score=min_score,
        )
```

- [ ] **Step 4: Update the docstring count**

Find the string `"14 tools"` (or `14 read-only tools`) in `livegraph/mcp/server.py` and replace it with `"15 tools"` (preserving wording). If you can't find the exact phrase, search:

```
grep -n "14" livegraph/mcp/server.py
```

and update only the count that refers to the registered tool list.

- [ ] **Step 5: Run the MCP server unit tests + the new neighborhood tests**

```
.venv/bin/python -m pytest tests/unit/test_mcp_server.py tests/unit/test_mcp_tools_semantic_neighborhood.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add livegraph/mcp/server.py
git commit -m "feat(phase9): register semantic_neighborhood as 15th MCP tool"
```

---

## Task 5: MCP server smoke test

**Files:**
- Modify: `tests/integration/test_mcp_server_smoke.py:32-41`

The current test asserts the registered tool names equal a sorted list of 14 names. Add `"semantic_neighborhood"`.

- [ ] **Step 1: Modify the assertion**

Open `tests/integration/test_mcp_server_smoke.py` and find:

```python
        assert tool_names == sorted([
            "find_symbol", "get_source",
            "find_callers", "find_callees",
            "runtime_only_calls", "dead_static_calls",
            "tests_for", "untested_symbols",
            "imports", "graph_status",
            "change_impact",
            "describe_schema", "run_cypher",
            "semantic_search",
        ])
```

Add `"semantic_neighborhood",` to the list (any position; the list is sorted).

- [ ] **Step 2: Run the test against Neo4j**

```
.venv/bin/python -m pytest tests/integration/test_mcp_server_smoke.py -v -m integration
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_mcp_server_smoke.py
git commit -m "test(phase9): smoke test sees the 15th MCP tool"
```

---

## Task 6: Integration test against real Neo4j + real MiniLM

**Files:**
- Create: `tests/integration/test_semantic_neighborhood_integration.py`

- [ ] **Step 1: Write the test file**

```python
"""semantic_neighborhood end-to-end against Neo4j + MiniLM."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.semantic]


@pytest.fixture()
def embedded_sample(ingested_sample):
    """Ingest sample, then run `livegraph embed` so the vector index exists."""
    from livegraph.semantic.embed import embed_project
    from livegraph.semantic.provider import LocalSTProvider

    backend, project = ingested_sample
    provider = LocalSTProvider(model_name="all-MiniLM-L6-v2", batch_size=8)
    embed_project(backend, project, provider)
    yield backend, project, provider


def test_addition_query_returns_calc_add_with_callers_and_tests(embedded_sample):
    from livegraph.mcp.tools_neighborhood import semantic_neighborhood

    backend, project, provider = embedded_sample
    result = semantic_neighborhood(
        backend, project, provider,
        query="addition arithmetic", limit=5,
    )
    assert result["warning"] is None
    qns = [r["qualified_name"] for r in result["results"]]
    add_qns = [q for q in qns if q and q.endswith(".add")]
    assert add_qns, f"expected an `.add` symbol in top-5, got {qns}"

    add_result = next(
        r for r in result["results"]
        if r["qualified_name"] and r["qualified_name"].endswith(".add")
    )
    # Sample project's Calculator.add has both callers (driver code) and
    # tests (the sample test suite). Both should be present.
    assert add_result["callers"], "expected at least one caller for .add"
    assert add_result["tests"], "expected at least one test for .add"
    # Provenance present on every caller/callee entry.
    for c in add_result["callers"]:
        assert c["provenance"] in {"static", "runtime", "both", None} or \
               isinstance(c["provenance"], str)


def test_include_callers_only_omits_other_fields(embedded_sample):
    from livegraph.mcp.tools_neighborhood import semantic_neighborhood

    backend, project, provider = embedded_sample
    result = semantic_neighborhood(
        backend, project, provider,
        query="addition arithmetic", limit=3, include=["callers"],
    )
    for r in result["results"]:
        assert "callers" in r
        assert "callees" not in r
        assert "tests" not in r
```

- [ ] **Step 2: Run with Neo4j + `[semantic]` installed**

```
.venv/bin/python -m pytest tests/integration/test_semantic_neighborhood_integration.py -v -m "integration and semantic"
```

Expected: 2 PASS (or skip if Neo4j unreachable / `[semantic]` missing).

- [ ] **Step 3: Run the full suite to confirm no regressions**

```
.venv/bin/python -m pytest -q
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_semantic_neighborhood_integration.py
git commit -m "test(phase9): semantic_neighborhood integration against Neo4j + MiniLM"
```

---

## Task 7: README section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Semantic neighborhood" section after the existing semantic-search section**

```markdown
## Semantic neighborhood (`semantic_neighborhood`)

For when an agent wants more than "what matches my query" — it wants
"where do I look and what do I run." The 15th MCP tool:

```
semantic_neighborhood(
    query: str,
    limit: int = 10,             # number of semantic seeds
    per_seed_limit: int = 10,    # cap per expansion list
    kind: str = "any",           # "any" | "function" | "method"
    include: list[str] | None = None,  # subset of {"callers","callees","tests"}
    min_score: float = 0.0,
)
```

For each top-K semantic match, returns the direct callers (with
`static`/`runtime`/`both` provenance), callees (same), and tests that
cover the symbol (Phase 2 coverage edges). One vector query + up to
three batched expansion queries — same latency budget as
`semantic_search` plus 3 Cypher round-trips.

Example agent prompt: *"Where does this codebase do JWT verification,
and what tests cover it?"* The agent calls `semantic_neighborhood`
once and gets matching functions, their call sites, and the tests it
should run to validate a change.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(phase9): README section for semantic_neighborhood"
```

---

## Acceptance gate (manual, before PR)

- [ ] `.venv/bin/python -m pytest -q` → all unit + integration tests pass.
- [ ] `.venv/bin/python -m ruff check .` → no new errors (compared to main).
- [ ] Manual via MCP client: with the sample project embedded, call `semantic_neighborhood("addition arithmetic", limit=3)` and confirm `Calculator.add` appears with its callers + tests attached.
- [ ] Manual graceful degradation: with `[semantic]` uninstalled, call `semantic_neighborhood(...)` and confirm `results=[]` + the install-hint warning.
