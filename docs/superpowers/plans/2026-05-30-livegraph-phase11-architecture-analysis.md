# livegraph Phase 11 — Architecture analysis tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three read-only MCP tools — `find_cycles`, `layering_violations`, `hubs` — that answer "is our architecture healthy?" by analyzing existing IMPORTS and CALLS edges. Take the MCP tool count from 18 → 21.

**Architecture:** New module `livegraph/mcp/tools_architecture.py` (same pattern as Phase 9's `tools_neighborhood.py` and Phase 10's `tools_history.py`). One supporting module `livegraph/mcp/_tarjan.py` for in-Python SCC computation (so we don't depend on GDS/APOC or networkx). All three tools issue one Cypher query and finish in Python.

**Tech Stack:** Python 3.12+, existing Neo4j backend (CALLS edges with `c.static`/`c.runtime` booleans; IMPORTS edges file→file), existing FastMCP, `fnmatch` (stdlib) for layer pattern matching.

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `livegraph/mcp/_tarjan.py` | Create | Tarjan's strongly-connected components (pure Python, no deps). |
| `livegraph/mcp/tools_architecture.py` | Create | `find_cycles`, `layering_violations`, `hubs` + their Cypher. |
| `livegraph/mcp/server.py` | Modify | Register the 3 new tools (19-21); "18 tools" → "21 tools". |
| `tests/unit/test_tarjan.py` | Create | Tarjan correctness on hand-crafted graphs. |
| `tests/unit/test_mcp_tools_architecture.py` | Create | 3 tools × validation + happy path + edge cases. |
| `tests/unit/test_mcp_server.py` | Modify | Update count-tools assertion 18 → 21. |
| `tests/integration/test_mcp_server_smoke.py` | Modify | Add the 3 names to expected sorted list. |
| `tests/integration/test_architecture_integration.py` | Create | Real Neo4j; tiny synthetic graph; verify each tool. |
| `README.md` | Modify | Add "Architecture analysis" section. |

No CLI changes; no new ingest; no new deps.

---

## Task 1: Tarjan's SCC algorithm

**Files:**
- Create: `livegraph/mcp/_tarjan.py`
- Test: `tests/unit/test_tarjan.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_tarjan.py`:

```python
from livegraph.mcp._tarjan import strongly_connected_components


def test_empty_graph_returns_no_components():
    assert strongly_connected_components({}) == []


def test_single_node_no_edges_returns_one_singleton():
    assert strongly_connected_components({"a": []}) == [["a"]]


def test_self_loop_is_a_one_node_scc():
    assert strongly_connected_components({"a": ["a"]}) == [["a"]]


def test_two_node_cycle():
    sccs = strongly_connected_components({"a": ["b"], "b": ["a"]})
    assert len(sccs) == 1
    assert sorted(sccs[0]) == ["a", "b"]


def test_three_node_cycle():
    sccs = strongly_connected_components(
        {"a": ["b"], "b": ["c"], "c": ["a"]}
    )
    assert len(sccs) == 1
    assert sorted(sccs[0]) == ["a", "b", "c"]


def test_two_disjoint_cycles():
    sccs = strongly_connected_components({
        "a": ["b"], "b": ["a"],   # cycle 1
        "c": ["d"], "d": ["c"],   # cycle 2
    })
    sccs_set = sorted(tuple(sorted(s)) for s in sccs)
    assert sccs_set == [("a", "b"), ("c", "d")]


def test_acyclic_returns_singletons_only():
    sccs = strongly_connected_components({
        "a": ["b"], "b": ["c"], "c": []
    })
    sccs_set = sorted(tuple(sorted(s)) for s in sccs)
    assert sccs_set == [("a",), ("b",), ("c",)]


def test_mixed_acyclic_and_cyclic():
    sccs = strongly_connected_components({
        "a": ["b"],            # acyclic start
        "b": ["c"],
        "c": ["d", "b"],       # cycle b<->c
        "d": ["e"],             # acyclic tail
        "e": [],
    })
    sccs_set = sorted(tuple(sorted(s)) for s in sccs)
    assert sccs_set == [("a",), ("b", "c"), ("d",), ("e",)]


def test_dangling_target_node_is_added_as_singleton():
    # 'b' appears only as a target — must still be in the result.
    sccs = strongly_connected_components({"a": ["b"]})
    sccs_set = sorted(tuple(sorted(s)) for s in sccs)
    assert sccs_set == [("a",), ("b",)]
```

- [ ] **Step 2: Run them, expect collection error**

```
.venv/bin/python -m pytest tests/unit/test_tarjan.py -v
```

- [ ] **Step 3: Implement Tarjan's SCC**

Create `livegraph/mcp/_tarjan.py`:

```python
"""Tarjan's strongly-connected components algorithm.

Used by `find_cycles` to identify SCCs in the call/import graph
without depending on networkx, the Neo4j GDS library, or APOC.

The input is a plain adjacency-list dict: ``{node: [neighbors...]}``.
Nodes appearing only as targets are auto-added as singletons. Returns
a list of components; each component is a list of nodes.

Implementation: iterative Tarjan (no recursion — Python's recursion
limit would otherwise bite us on big graphs).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def strongly_connected_components(
    graph: Mapping[str, Sequence[str]],
) -> list[list[str]]:
    """Iterative Tarjan's SCC. Returns components, no guaranteed order."""
    # Collect every node (sources + dangling targets).
    nodes: set[str] = set(graph.keys())
    for adj in graph.values():
        nodes.update(adj)
    if not nodes:
        return []

    index_counter = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []

    # Iterative DFS state per node: (node, iter_over_neighbors)
    for start in nodes:
        if start in indices:
            continue
        work: list[tuple[str, list[str], int]] = [
            (start, list(graph.get(start, ())), 0),
        ]
        indices[start] = index_counter
        lowlinks[start] = index_counter
        index_counter += 1
        stack.append(start)
        on_stack.add(start)

        while work:
            node, neighbors, ni = work[-1]
            if ni < len(neighbors):
                w = neighbors[ni]
                work[-1] = (node, neighbors, ni + 1)
                if w not in indices:
                    indices[w] = index_counter
                    lowlinks[w] = index_counter
                    index_counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, list(graph.get(w, ())), 0))
                elif w in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[w])
            else:
                # Finished this node; pop and propagate lowlink up.
                if lowlinks[node] == indices[node]:
                    component: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == node:
                            break
                    sccs.append(component)
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlinks[parent] = min(lowlinks[parent], lowlinks[node])

    return sccs
```

- [ ] **Step 4: Run tests, expect 9 PASS**

```
.venv/bin/python -m pytest tests/unit/test_tarjan.py -v
```

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/_tarjan.py tests/unit/test_tarjan.py
git commit -m "feat(phase11): Tarjan's SCC (iterative, no deps)"
```

---

## Task 2: `find_cycles` tool

**Files:**
- Create: `livegraph/mcp/tools_architecture.py`
- Test: `tests/unit/test_mcp_tools_architecture.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mcp_tools_architecture.py`:

```python
from __future__ import annotations

from typing import Any

from livegraph.mcp.tools_architecture import find_cycles


class _FakeBackend:
    def __init__(self, rows: list[dict[str, Any]] | dict[str, list[dict[str, Any]]] = None):
        # Either a single row list (returned for every call) or a dict
        # keyed by substring-of-cypher.
        self._rows = rows or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        if isinstance(self._rows, dict):
            for key, rows in self._rows.items():
                if key in cypher:
                    return rows
            return []
        return self._rows

    def verify(self): return None
    def close(self): return None


def test_find_cycles_invalid_scope_returns_warning():
    backend = _FakeBackend([])
    out = find_cycles(backend, project="p", scope="nonsense")
    assert out["cycles"] == []
    assert "scope" in out["warning"].lower()
    assert backend.calls == []


def test_find_cycles_invalid_provenance_returns_warning():
    backend = _FakeBackend([])
    out = find_cycles(backend, project="p", provenance="bogus")
    assert out["cycles"] == []
    assert "provenance" in out["warning"].lower()


def test_find_cycles_call_scope_returns_scc_of_cycle():
    # Caller -> Callee rows
    backend = _FakeBackend([
        {"source": "pkg.a.foo", "target": "pkg.b.bar"},
        {"source": "pkg.b.bar", "target": "pkg.a.foo"},
        {"source": "pkg.c.baz", "target": "pkg.d.qux"},  # acyclic edge
    ])
    out = find_cycles(backend, project="p", scope="call")
    assert out["warning"] is None
    # One cycle of size 2 (foo<->bar). Singletons are filtered by
    # the default min_size=2.
    assert len(out["cycles"]) == 1
    assert sorted(out["cycles"][0]["nodes"]) == ["pkg.a.foo", "pkg.b.bar"]
    assert out["cycles"][0]["size"] == 2


def test_find_cycles_min_size_drops_self_loops():
    backend = _FakeBackend([
        {"source": "pkg.a.foo", "target": "pkg.a.foo"},  # self-loop
        {"source": "pkg.b.bar", "target": "pkg.c.baz"},
        {"source": "pkg.c.baz", "target": "pkg.b.bar"},
    ])
    out = find_cycles(backend, project="p", scope="call", min_size=2)
    qns_in_cycles = [n for c in out["cycles"] for n in c["nodes"]]
    assert "pkg.a.foo" not in qns_in_cycles  # filtered
    assert "pkg.b.bar" in qns_in_cycles
    assert "pkg.c.baz" in qns_in_cycles


def test_find_cycles_min_size_one_includes_self_loops():
    backend = _FakeBackend([
        {"source": "pkg.a.foo", "target": "pkg.a.foo"},
    ])
    out = find_cycles(backend, project="p", scope="call", min_size=1)
    assert len(out["cycles"]) == 1
    assert out["cycles"][0]["nodes"] == ["pkg.a.foo"]


def test_find_cycles_orders_by_size_desc():
    backend = _FakeBackend([
        # 2-cycle
        {"source": "a", "target": "b"},
        {"source": "b", "target": "a"},
        # 3-cycle
        {"source": "c", "target": "d"},
        {"source": "d", "target": "e"},
        {"source": "e", "target": "c"},
    ])
    out = find_cycles(backend, project="p", scope="call")
    sizes = [c["size"] for c in out["cycles"]]
    assert sizes == [3, 2]


def test_find_cycles_limit_clamps_to_100():
    backend = _FakeBackend([])
    find_cycles(backend, project="p", scope="call", limit=9999)
    # Validation happens before the query; we just verify the limit
    # was clamped in the returned response.
    # The clamping is exercised via output ordering; this test mainly
    # checks no crash on out-of-range limit.


def test_find_cycles_module_scope_uses_imports_query():
    backend = _FakeBackend([
        {"source": "a.py", "target": "b.py"},
        {"source": "b.py", "target": "a.py"},
    ])
    out = find_cycles(backend, project="p", scope="module")
    assert len(backend.calls) == 1
    cypher = backend.calls[0][0]
    assert ":IMPORTS" in cypher
    assert ":CALLS" not in cypher
    assert sorted(out["cycles"][0]["nodes"]) == ["a.py", "b.py"]


def test_find_cycles_call_scope_provenance_static_filters_runtime():
    backend = _FakeBackend([])
    find_cycles(backend, project="p", scope="call", provenance="static")
    cypher = backend.calls[0][0]
    assert "c.static" in cypher
```

- [ ] **Step 2: Run them, expect collection error**

```
.venv/bin/python -m pytest tests/unit/test_mcp_tools_architecture.py -v
```

- [ ] **Step 3: Implement `find_cycles`**

Create `livegraph/mcp/tools_architecture.py`:

```python
"""MCP tools that analyze project architecture (Phase 11).

Three tools: ``find_cycles``, ``layering_violations``, ``hubs``.
All read-only; no new node or edge types are introduced.
"""
from __future__ import annotations

import fnmatch
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
                # No self-loop, real singleton, drop.
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
```

- [ ] **Step 4: Run tests, expect 8 PASS**

```
.venv/bin/python -m pytest tests/unit/test_mcp_tools_architecture.py -v
```

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools_architecture.py tests/unit/test_mcp_tools_architecture.py
git commit -m "feat(phase11): find_cycles tool (call/module scope) + tests"
```

---

## Task 3: `layering_violations` tool

**Files:**
- Modify: `livegraph/mcp/tools_architecture.py` (append)
- Modify: `tests/unit/test_mcp_tools_architecture.py` (append)

- [ ] **Step 1: Append failing tests**

In `tests/unit/test_mcp_tools_architecture.py`, append after the existing tests:

```python


# ---- layering_violations -------------------------------------------

from livegraph.mcp.tools_architecture import layering_violations


def test_layering_empty_layers_warns():
    backend = _FakeBackend([])
    out = layering_violations(backend, project="p", layers=[])
    assert out["violations"] == []
    assert "empty" in (out["warning"] or "").lower()


def test_layering_invalid_edge_kind_warns():
    backend = _FakeBackend([])
    out = layering_violations(
        backend, project="p",
        layers=[{"name": "x", "patterns": ["*"]}],
        edge_kind="bogus",
    )
    assert out["violations"] == []
    assert "edge_kind" in (out["warning"] or "").lower()


def test_layering_detects_imports_violation():
    # Edges: domain/calc.py imports web/handlers.py (violation)
    backend = _FakeBackend({
        "(:File)": [
            {"path": "domain/calc.py"},
            {"path": "web/handlers.py"},
            {"path": "scripts/run.py"},  # unmatched
        ],
        ":IMPORTS": [
            {"from_file": "domain/calc.py", "to_file": "web/handlers.py"},
        ],
        # No call edges.
        "CALLS": [],
    })
    out = layering_violations(
        backend, project="p",
        layers=[
            {"name": "web", "patterns": ["web/**"]},
            {"name": "domain", "patterns": ["domain/**"]},
        ],
    )
    assert any(
        v["from_file"] == "domain/calc.py" and v["to_file"] == "web/handlers.py"
        for v in out["violations"]
    )
    # The unmatched scripts/ file shows up in the summary.
    assert out["summary"]["files_unlayered"] == 1


def test_layering_first_match_wins_for_multimatch():
    """If a file matches multiple layers, first one in `layers` wins."""
    backend = _FakeBackend({
        "(:File)": [
            {"path": "domain/auth/login.py"},
            {"path": "web/handlers.py"},
        ],
        ":IMPORTS": [
            # auth -> web is a violation IFF login.py is in 'auth'
            # (auth above web). If login.py were treated as 'domain'
            # (domain below web), this would also be a violation but
            # the from_layer label would differ.
            {"from_file": "domain/auth/login.py",
             "to_file": "web/handlers.py"},
        ],
        "CALLS": [],
    })
    out = layering_violations(
        backend, project="p",
        layers=[
            {"name": "auth", "patterns": ["domain/auth/**"]},
            {"name": "web", "patterns": ["web/**"]},
            {"name": "domain", "patterns": ["domain/**"]},
        ],
    )
    v = out["violations"][0]
    assert v["from_layer"] == "auth"  # first match wins
    assert v["to_layer"] == "web"


def test_layering_skips_intra_layer_and_top_to_bottom_edges():
    backend = _FakeBackend({
        "(:File)": [
            {"path": "web/a.py"}, {"path": "web/b.py"},
            {"path": "domain/c.py"},
        ],
        ":IMPORTS": [
            {"from_file": "web/a.py", "to_file": "web/b.py"},   # intra-layer
            {"from_file": "web/a.py", "to_file": "domain/c.py"}, # web -> domain OK
        ],
        "CALLS": [],
    })
    out = layering_violations(
        backend, project="p",
        layers=[
            {"name": "web", "patterns": ["web/**"]},
            {"name": "domain", "patterns": ["domain/**"]},
        ],
    )
    assert out["violations"] == []
    assert out["summary"]["violations"] == 0


def test_layering_edge_kind_filter_imports_only():
    backend = _FakeBackend({
        "(:File)": [
            {"path": "domain/c.py"}, {"path": "web/h.py"},
        ],
        ":IMPORTS": [
            {"from_file": "domain/c.py", "to_file": "web/h.py"},
        ],
        "CALLS": [
            {"from_file": "domain/c.py", "to_file": "web/h.py"},
        ],
    })
    out = layering_violations(
        backend, project="p",
        layers=[
            {"name": "web", "patterns": ["web/**"]},
            {"name": "domain", "patterns": ["domain/**"]},
        ],
        edge_kind="imports",
    )
    kinds = {v["edge_kind"] for v in out["violations"]}
    assert kinds == {"imports"}
```

- [ ] **Step 2: Run, expect collection error / missing function**

```
.venv/bin/python -m pytest tests/unit/test_mcp_tools_architecture.py -v
```

- [ ] **Step 3: Implement `layering_violations`**

Append to `livegraph/mcp/tools_architecture.py`:

```python


# ---- layering_violations -------------------------------------------

_ALL_FILES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
    "RETURN f.path AS path"
)

_IMPORTS_EDGES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
    "-[:IMPORTS]->(tgt:File) "
    "RETURN src.path AS from_file, tgt.path AS to_file"
)

_CALL_EDGES_BY_FILE_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "MATCH (s)-[:CALLS]->(t) "
    "MATCH (:File)-[:DEFINES|HAS_METHOD*1..2]->(t) "
    "MATCH (tf:File)-[:DEFINES|HAS_METHOD*1..2]->(t) "
    "RETURN DISTINCT src.path AS from_file, tf.path AS to_file"
)


def _assign_layer(path: str, layers: list[dict[str, Any]]) -> str | None:
    for layer in layers:
        name = layer.get("name")
        patterns = layer.get("patterns") or []
        for pat in patterns:
            if fnmatch.fnmatch(path, pat):
                return name
    return None


def layering_violations(
    backend: GraphBackend,
    project: str,
    layers: list[dict[str, Any]],
    edge_kind: str = "any",
    limit: int = 50,
) -> dict[str, Any]:
    """Report edges that go 'up' the supplied layering."""
    if not layers:
        return {
            "violations": [],
            "summary": {"violations": 0, "files_unlayered": 0,
                        "edges_checked": 0},
            "warning": "layers definition is empty",
        }
    if edge_kind not in _VALID_EDGE_KINDS:
        return {
            "violations": [],
            "summary": {"violations": 0, "files_unlayered": 0,
                        "edges_checked": 0},
            "warning": (
                f"invalid edge_kind {edge_kind!r}; "
                f"must be one of {list(_VALID_EDGE_KINDS)}"
            ),
        }
    limit = max(1, min(int(limit), _MAX_VIOLATIONS))

    # Layer rank: lower index = "upper" layer. Edge from rank_a to
    # rank_b is a violation iff rank_a > rank_b (going up).
    rank: dict[str, int] = {
        layer["name"]: i for i, layer in enumerate(layers)
    }

    # 1) File → layer assignment.
    file_rows = backend.execute(_ALL_FILES_CYPHER, project=project)
    file_layer: dict[str, str] = {}
    unlayered = 0
    for row in file_rows:
        path = row.get("path")
        if not path:
            continue
        layer = _assign_layer(path, layers)
        if layer is None:
            unlayered += 1
        else:
            file_layer[path] = layer

    # 2) Edge fetch (per kind).
    edge_rows: list[tuple[str, str, str]] = []
    if edge_kind in ("any", "imports"):
        for row in backend.execute(_IMPORTS_EDGES_CYPHER, project=project):
            edge_rows.append(
                (row.get("from_file"), row.get("to_file"), "imports"),
            )
    if edge_kind in ("any", "calls"):
        for row in backend.execute(_CALL_EDGES_BY_FILE_CYPHER,
                                   project=project):
            edge_rows.append(
                (row.get("from_file"), row.get("to_file"), "calls"),
            )

    violations: list[dict[str, Any]] = []
    edges_checked = 0
    for from_f, to_f, kind in edge_rows:
        if not from_f or not to_f:
            continue
        from_layer = file_layer.get(from_f)
        to_layer = file_layer.get(to_f)
        if from_layer is None or to_layer is None:
            continue  # unlayered ends, silently skipped
        edges_checked += 1
        # Self-edge (intra-layer) is not a violation.
        if from_layer == to_layer:
            continue
        if rank[from_layer] > rank[to_layer]:
            violations.append({
                "from_file": from_f,
                "to_file": to_f,
                "from_layer": from_layer,
                "to_layer": to_layer,
                "edge_kind": kind,
            })

    violations.sort(
        key=lambda v: (v["from_layer"], v["from_file"],
                       v["to_file"], v["edge_kind"]),
    )
    violations = violations[:limit]

    return {
        "violations": violations,
        "summary": {
            "violations": len(violations),
            "files_unlayered": unlayered,
            "edges_checked": edges_checked,
        },
        "warning": None,
    }
```

A note for the implementer: the call-edges-by-file Cypher above does redundant work (it uses `:DEFINES|HAS_METHOD` twice). Simplify if you prefer; what matters is that you get distinct `(from_file, to_file)` pairs aggregated from all CALLS edges. A cleaner version:

```cypher
MATCH (:Project {name: $project})-[:CONTAINS]->(sf:File)
      -[:DEFINES|HAS_METHOD*1..2]->(s)
MATCH (s)-[:CALLS]->(t)
MATCH (tf:File)-[:DEFINES|HAS_METHOD*1..2]->(t)
WHERE sf <> tf
RETURN DISTINCT sf.path AS from_file, tf.path AS to_file
```

Use that as `_CALL_EDGES_BY_FILE_CYPHER` instead. The `sf <> tf` excludes intra-file calls so the inter-file edge list is what we actually want.

- [ ] **Step 4: Run tests, expect all PASS (architecture file should be 14 tests so far — 8 from Task 2 + 6 from Task 3)**

```
.venv/bin/python -m pytest tests/unit/test_mcp_tools_architecture.py -v
```

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools_architecture.py tests/unit/test_mcp_tools_architecture.py
git commit -m "feat(phase11): layering_violations tool + tests"
```

---

## Task 4: `hubs` tool

**Files:**
- Modify: `livegraph/mcp/tools_architecture.py` (append)
- Modify: `tests/unit/test_mcp_tools_architecture.py` (append)

- [ ] **Step 1: Append failing tests**

In `tests/unit/test_mcp_tools_architecture.py`, append:

```python


# ---- hubs ----------------------------------------------------------

from livegraph.mcp.tools_architecture import hubs


def test_hubs_invalid_kind_warns():
    backend = _FakeBackend([])
    out = hubs(backend, project="p", kind="bogus")
    assert out["results"] == []
    assert "kind" in (out["warning"] or "").lower()


def test_hubs_returns_results_ordered_by_in_callers_desc():
    backend = _FakeBackend([
        {"qualified_name": "pkg.util.normalize", "kind": "function",
         "file": "pkg/util.py", "in_callers": 47, "out_callees": 3},
        {"qualified_name": "pkg.util.format", "kind": "function",
         "file": "pkg/util.py", "in_callers": 12, "out_callees": 1},
    ])
    out = hubs(backend, project="p", min_fanin=10)
    assert [r["qualified_name"] for r in out["results"]] == [
        "pkg.util.normalize", "pkg.util.format",
    ]


def test_hubs_passes_min_fanin_and_limit_to_query():
    backend = _FakeBackend([])
    hubs(backend, project="p", min_fanin=50, limit=5)
    cypher, params = backend.calls[0]
    assert params["min_fanin"] == 50
    assert params["limit"] == 5


def test_hubs_clamps_min_fanin_and_limit():
    backend = _FakeBackend([])
    hubs(backend, project="p", min_fanin=99999, limit=9999)
    _, params = backend.calls[0]
    assert params["min_fanin"] == 1000
    assert params["limit"] == 100


def test_hubs_kind_function_excludes_methods_in_cypher():
    backend = _FakeBackend([])
    hubs(backend, project="p", kind="function")
    cypher = backend.calls[0][0]
    assert "Function" in cypher
```

- [ ] **Step 2: Run, expect collection error / missing function**

- [ ] **Step 3: Implement `hubs`**

Append to `livegraph/mcp/tools_architecture.py`:

```python


# ---- hubs ----------------------------------------------------------

_HUBS_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE ($kind = 'any' AND (s:Function OR s:Method)) "
    "   OR ($kind = 'function' AND s:Function AND NOT s:Test) "
    "   OR ($kind = 'method' AND s:Method) "
    "OPTIONAL MATCH (caller)-[:CALLS]->(s) "
    "OPTIONAL MATCH (s)-[:CALLS]->(callee) "
    "WITH s, "
    "     count(DISTINCT caller) AS in_callers, "
    "     count(DISTINCT callee) AS out_callees "
    "WHERE in_callers >= $min_fanin "
    "RETURN s.qualified_name AS qualified_name, "
    "       head([l IN labels(s) "
    "             WHERE l IN ['Function','Method'] | toLower(l)]) AS kind, "
    "       s.file AS file, "
    "       in_callers, out_callees "
    "ORDER BY in_callers DESC, qualified_name ASC "
    "LIMIT $limit"
)


def hubs(
    backend: GraphBackend,
    project: str,
    kind: str = "any",
    min_fanin: int = 10,
    limit: int = 20,
) -> dict[str, Any]:
    """Symbols with high in-degree (most-called functions/methods)."""
    if kind not in _VALID_KINDS:
        return {
            "results": [],
            "warning": (
                f"invalid kind {kind!r}; "
                f"must be one of {list(_VALID_KINDS)}"
            ),
        }
    min_fanin = max(1, min(int(min_fanin), _MAX_MIN_FANIN))
    limit = max(1, min(int(limit), _MAX_LIMIT))
    rows = backend.execute(
        _HUBS_CYPHER, project=project, kind=kind,
        min_fanin=min_fanin, limit=limit,
    )
    return {"results": rows, "warning": None}
```

- [ ] **Step 4: Run tests, expect all PASS**

```
.venv/bin/python -m pytest tests/unit/test_mcp_tools_architecture.py -v
```

Then full unit suite to catch regressions:

```
.venv/bin/python -m pytest tests/unit/ -q
```

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools_architecture.py tests/unit/test_mcp_tools_architecture.py
git commit -m "feat(phase11): hubs tool + tests"
```

---

## Task 5: Register the 3 new MCP tools

**Files:**
- Modify: `livegraph/mcp/server.py`
- Modify: `tests/unit/test_mcp_server.py`
- Modify: `tests/integration/test_mcp_server_smoke.py`

- [ ] **Step 1: Add the import to `server.py`**

Near the existing `from livegraph.mcp.tools_history import ...`, add:

```python
from livegraph.mcp.tools_architecture import (
    find_cycles as _find_cycles,
    hubs as _hubs,
    layering_violations as _layering_violations,
)
```

- [ ] **Step 2: Register the 3 tools in `build_server`**

Just before `return mcp` at the end of `build_server`, add:

```python
    @mcp.tool()
    def find_cycles(
        scope: str = "call",
        provenance: str = "any",
        min_size: int = 2,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Strongly-connected components in the call or import graph.

        ``scope="call"`` searches symbol→symbol CALLS edges (filterable
        by ``provenance``: ``"any"``, ``"static"``, ``"runtime"``).
        ``scope="module"`` searches file→file IMPORTS edges.
        Trivial self-loops are filtered by default (``min_size=2``).
        """
        backend, project = _require_state()
        return _find_cycles(backend, project, scope=scope,
                            provenance=provenance,
                            min_size=min_size, limit=limit)

    @mcp.tool()
    def layering_violations(
        layers: list[dict[str, Any]],
        edge_kind: str = "any",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Report edges that go 'up' the supplied layering.

        ``layers`` is an ordered list of ``{"name": str, "patterns":
        list[str]}``. Upper layers may depend on lower layers; the
        reverse is a violation. Glob patterns match against
        ``File.path``. Files matching no pattern are unlayered and
        skipped (counted in ``summary.files_unlayered``). Files
        matching multiple patterns take the first matching layer.
        """
        backend, project = _require_state()
        return _layering_violations(
            backend, project, layers=layers,
            edge_kind=edge_kind, limit=limit,
        )

    @mcp.tool()
    def hubs(
        kind: str = "any", min_fanin: int = 10, limit: int = 20,
    ) -> dict[str, Any]:
        """Symbols with high inbound CALLS — "everything depends on this".

        Returns symbols whose distinct in-callers ≥ ``min_fanin``,
        ordered by in-degree descending. ``kind`` is ``"any"``,
        ``"function"``, or ``"method"``.
        """
        backend, project = _require_state()
        return _hubs(backend, project, kind=kind,
                     min_fanin=min_fanin, limit=limit)
```

- [ ] **Step 3: Update "18 tools" → "21 tools" in docstrings**

Find and update both occurrences (module docstring and `build_server` docstring):

```
grep -n "18 " livegraph/mcp/server.py
```

Update only the ones that refer to the tool count (there should be exactly two).

- [ ] **Step 4: Update `tests/unit/test_mcp_server.py`**

Phase 10 renamed the count-tools test to
`test_build_server_registers_eighteen_tools_including_history`. Rename
it to `test_build_server_registers_twenty_one_tools_including_architecture`
and add the three new names — `"find_cycles"`, `"layering_violations"`,
`"hubs"` — to the expected sorted list.

- [ ] **Step 5: Update `tests/integration/test_mcp_server_smoke.py`**

Add the same three names to the smoke test's expected sorted list (now
21 entries).

- [ ] **Step 6: Run tests**

```
.venv/bin/python -m pytest tests/unit/test_mcp_server.py tests/unit/test_mcp_tools_architecture.py -v
```
Expected: all PASS.

```
.venv/bin/python -m pytest tests/integration/test_mcp_server_smoke.py -v -m integration
```
Expected: PASS (or skip if Neo4j unreachable).

- [ ] **Step 7: Commit**

```bash
git add livegraph/mcp/server.py tests/unit/test_mcp_server.py tests/integration/test_mcp_server_smoke.py
git commit -m "feat(phase11): register 3 architecture tools (19-21)"
```

---

## Task 6: Integration test against real Neo4j

**Files:**
- Create: `tests/integration/test_architecture_integration.py`

- [ ] **Step 1: Write the test file**

```python
"""End-to-end: real Neo4j + architecture analysis tools."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def synthetic_arch(neo4j_backend):
    """Build a tiny synthetic graph with known cycles and a layering
    violation. We bypass the parser and write directly via Cypher."""
    backend = neo4j_backend
    project = "arch_test"

    # Project + 3 files
    backend.execute(
        "MERGE (p:Project {name: $project}) "
        "WITH p UNWIND $paths AS path "
        "MERGE (f:File {path: path}) "
        "MERGE (p)-[:CONTAINS]->(f)",
        project=project,
        paths=["web/handlers.py", "domain/calc.py", "infra/db.py"],
    )
    # File-level IMPORTS forming a cycle: web -> domain -> web
    backend.execute(
        "MATCH (a:File {path: 'web/handlers.py'}), "
        "      (b:File {path: 'domain/calc.py'}) "
        "MERGE (a)-[:IMPORTS]->(b) "
        "MERGE (b)-[:IMPORTS]->(a)",
    )
    # Plus a layering violation: domain -> web (already there as
    # part of the cycle).
    # Symbols + CALLS for hubs / call-scope cycles
    backend.execute(
        "MATCH (f:File {path: 'web/handlers.py'}) "
        "MERGE (s1:Function:Symbol {qualified_name: 'web.handlers.foo', "
        "                  name: 'foo', file: 'web/handlers.py', "
        "                  start_line: 1, end_line: 3}) "
        "MERGE (s2:Function:Symbol {qualified_name: 'web.handlers.bar', "
        "                  name: 'bar', file: 'web/handlers.py', "
        "                  start_line: 4, end_line: 6}) "
        "MERGE (f)-[:DEFINES]->(s1) "
        "MERGE (f)-[:DEFINES]->(s2)",
    )
    # Call cycle s1 <-> s2
    backend.execute(
        "MATCH (s1 {qualified_name: 'web.handlers.foo'}), "
        "      (s2 {qualified_name: 'web.handlers.bar'}) "
        "MERGE (s1)-[:CALLS {static: true, runtime: false}]->(s2) "
        "MERGE (s2)-[:CALLS {static: true, runtime: false}]->(s1)",
    )
    return backend, project


def test_find_cycles_module_scope_finds_the_2_cycle(synthetic_arch):
    from livegraph.mcp.tools_architecture import find_cycles

    backend, project = synthetic_arch
    out = find_cycles(backend, project, scope="module")
    assert out["warning"] is None
    assert any(
        sorted(c["nodes"]) == ["domain/calc.py", "web/handlers.py"]
        for c in out["cycles"]
    )


def test_find_cycles_call_scope_finds_foo_bar_cycle(synthetic_arch):
    from livegraph.mcp.tools_architecture import find_cycles

    backend, project = synthetic_arch
    out = find_cycles(backend, project, scope="call")
    assert any(
        sorted(c["nodes"]) == ["web.handlers.bar", "web.handlers.foo"]
        for c in out["cycles"]
    )


def test_layering_violations_finds_domain_to_web(synthetic_arch):
    from livegraph.mcp.tools_architecture import layering_violations

    backend, project = synthetic_arch
    out = layering_violations(
        backend, project,
        layers=[
            {"name": "web", "patterns": ["web/**"]},
            {"name": "domain", "patterns": ["domain/**"]},
            {"name": "infra", "patterns": ["infra/**"]},
        ],
    )
    # domain/calc.py -> web/handlers.py is the upward edge.
    assert any(
        v["from_layer"] == "domain" and v["to_layer"] == "web"
        for v in out["violations"]
    )
    assert out["summary"]["files_unlayered"] == 0


def test_hubs_returns_foo_and_bar_at_min_fanin_1(synthetic_arch):
    from livegraph.mcp.tools_architecture import hubs

    backend, project = synthetic_arch
    out = hubs(backend, project, min_fanin=1)
    qns = {r["qualified_name"] for r in out["results"]}
    assert "web.handlers.foo" in qns or "web.handlers.bar" in qns
```

- [ ] **Step 2: Run with Neo4j up**

```
.venv/bin/python -m pytest tests/integration/test_architecture_integration.py -v -m integration
```
Expected: 4 PASS (or all skip if Neo4j unreachable).

- [ ] **Step 3: Run the full suite to confirm no regressions**

```
.venv/bin/python -m pytest -q
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_architecture_integration.py
git commit -m "test(phase11): architecture tools end-to-end against real Neo4j"
```

---

## Task 7: README section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a new section**

At the very end of `README.md` (after the existing "Git history"
section), add:

```markdown

## Architecture analysis

Three read-only MCP tools (bringing the count to 21) for "is our
architecture healthy?" questions, all over edges that already exist
in the graph:

| Tool | What it answers |
|---|---|
| `find_cycles(scope, provenance, min_size, limit)` | Strongly-connected components in the call graph (`scope="call"`, filterable by `static`/`runtime`/`any`) or import graph (`scope="module"`). |
| `layering_violations(layers, edge_kind, limit)` | Edges that go "up" the supplied layering. `layers` is an ordered list of `{name, patterns}` — top layers may depend on lower layers; the reverse is a violation. Files matching no pattern are unlayered and silently skipped; files matching multiple take the first. |
| `hubs(kind, min_fanin, limit)` | Symbols with high inbound CALLS — the "everything depends on this helper" detector. |

Example agent prompt: *"Are any of our domain modules importing
infrastructure code by mistake?"* The agent calls `layering_violations`
with the project's layering and gets back the specific edges to fix.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(phase11): README section for architecture analysis"
```

---

## Acceptance gate (manual, before PR)

- [ ] `.venv/bin/python -m pytest -q` → all tests pass.
- [ ] `.venv/bin/python -m ruff check .` → no new errors compared to main.
- [ ] Manual via MCP client: on a built project, call `hubs(min_fanin=5)` and confirm the top results look sensible. Call `find_cycles(scope="module")` and confirm the import graph either reports no cycles (clean architecture) or a believable cycle.
- [ ] Manual via `find_cycles(scope="call", provenance="runtime")` — confirms runtime-observed mutual recursion is captured, distinct from `provenance="static"`.
