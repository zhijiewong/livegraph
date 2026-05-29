# livegraph Phase 11 — architecture analysis tools (design)

**Date:** 2026-05-30
**Status:** Approved

## Goal

Add three read-only MCP tools (`find_cycles`, `layering_violations`,
`hubs`) that answer the "is our architecture healthy?" question using
edges already in the graph. No new ingest. Takes the MCP tool count
from 18 → 21.

## Non-goals

- New node or edge types.
- New CLI commands.
- Heavyweight algorithms (PageRank, community detection). The three
  tools cover the questions agents actually ask; richer analysis can
  layer on later via `run_cypher`.
- Auto-suggesting a layering definition. The agent provides it.

## Tool surface

### `find_cycles(scope="call", provenance="any", min_size=2, limit=20)`

Strongly-connected components in either the call graph or the import
graph. Trivial self-loops are filtered out by default (`min_size=2`).

- `scope`: `"call"` (symbol→symbol CALLS edges) or `"module"`
  (file→file IMPORTS edges).
- `provenance` (only meaningful when `scope="call"`): `"any"`,
  `"static"`, `"runtime"`. Same semantics as `find_callers`.
- `min_size`: minimum cycle size to return (default 2; clamped to
  `[1, 100]`).
- `limit`: number of cycles returned (default 20; clamped to
  `[1, 100]`).

Returns:
```json
{
  "scope": "call",
  "provenance": "any",
  "cycles": [
    {
      "size": 3,
      "nodes": ["pkg.a.foo", "pkg.b.bar", "pkg.c.baz"]
    }
  ],
  "warning": null
}
```

Ordered by cycle size descending, then by lexically-smallest node
ascending (stable). Nodes within a cycle are sorted lexically (the
SCC doesn't have a canonical entry point; sorting makes the output
diff-friendly).

### `layering_violations(layers, edge_kind="any", limit=50)`

Reports edges that violate the user-supplied layering.

- `layers`: ordered list of `{name: str, patterns: list[str]}`. Top
  layers may depend on lower layers; the reverse is a violation.
  Glob patterns are matched against `File.path`.
- `edge_kind`: `"any"` (default), `"imports"`, or `"calls"`. `"any"`
  checks both edge sets.
- `limit`: max violations returned (default 50; clamped to
  `[1, 200]`).

Returns:
```json
{
  "violations": [
    {
      "from_file": "domain/calc.py",
      "to_file": "web/handlers.py",
      "from_layer": "domain",
      "to_layer": "web",
      "edge_kind": "imports"
    }
  ],
  "summary": {
    "violations": 12,
    "files_unlayered": 47,
    "edges_checked": 1340
  },
  "warning": null
}
```

Resolution rules:
- A file with no matching layer is treated as **unlayered** (skipped
  silently, counted in `summary.files_unlayered`).
- A file matching multiple layers gets the **first** matching layer
  in the supplied order. Agents can express specificity by putting
  more specific layers first.
- If `edge_kind="any"` and the same pair (from_file, to_file)
  violates via both imports and calls, both rows are returned
  (separate `edge_kind` values).

### `hubs(kind="any", min_fanin=10, limit=20)`

Symbols with high inbound CALLS — the "everything depends on this
helper" detector.

- `kind`: `"any"` (default), `"function"`, `"method"`.
- `min_fanin`: minimum distinct in-callers (default 10; clamped to
  `[1, 1000]`).
- `limit`: top-K (default 20; clamped to `[1, 100]`).

Returns:
```json
{
  "results": [
    {
      "qualified_name": "pkg.util.normalize",
      "kind": "function",
      "file": "pkg/util.py",
      "in_callers": 47,
      "out_callees": 3
    }
  ],
  "warning": null
}
```

Ordered by `in_callers DESC`, ties broken by `qualified_name ASC`.

## Architecture

New file: `livegraph/mcp/tools_architecture.py` (same pattern as
Phase 9's `tools_neighborhood.py` and Phase 10's `tools_history.py` —
keeps `tools.py` focused).

The implementation is mostly Cypher. The two interesting bits:

- **`find_cycles`** uses Neo4j 5's built-in
  `gds.alpha.scc` if available, otherwise falls back to a pure-Cypher
  cycle search via `apoc.algo.cypher`/path repeat. Neither is
  guaranteed installed. **Decision**: don't depend on either —
  implement SCC discovery in Python after pulling the edge list, just
  like networkx does. The dataset is small (project-scoped), so an
  in-memory Tarjan's algorithm fits easily.
- **`layering_violations`** runs glob-matching in Python (we already
  have `File.path` cheaply), then issues a single Cypher query to
  fetch all edges between layered files, then filters in Python.

For the SCC implementation we add `livegraph/mcp/_tarjan.py` — a
small standalone Tarjan's algorithm so we don't pick up networkx as
a runtime dep.

## File map

| File | Action | Responsibility |
|---|---|---|
| `livegraph/mcp/_tarjan.py` | Create | Tarjan's strongly-connected components (pure Python, no deps). |
| `livegraph/mcp/tools_architecture.py` | Create | `find_cycles`, `layering_violations`, `hubs` + Cypher. |
| `livegraph/mcp/server.py` | Modify | Register the 3 new tools (19–21). "18 tools" → "21 tools". |
| `tests/integration/test_mcp_server_smoke.py` | Modify | Add the 3 names. |
| `tests/unit/test_mcp_server.py` | Modify | Update the count-tools assertion. |
| `tests/unit/test_tarjan.py` | Create | SCC algorithm correctness. |
| `tests/unit/test_mcp_tools_architecture.py` | Create | 3 tools × happy path + edge cases. |
| `tests/integration/test_architecture_integration.py` | Create | Real Neo4j; build a small cyclic graph; verify each tool. |
| `README.md` | Modify | "Architecture analysis" section. |

No CLI changes; no new ingest; no new dependencies.

## Error handling

| Source | Behavior |
|---|---|
| Empty graph / no project | `cycles=[]`, `violations=[]`, `results=[]` + `warning="no project data; run livegraph build"`. |
| `find_cycles` `scope` invalid | `cycles=[]`, structured warning listing valid values. |
| `layering_violations` empty `layers` | `violations=[]`, `warning="layers definition is empty"`. |
| `layering_violations` `edge_kind` invalid | `violations=[]`, structured warning. |
| `hubs` `kind` invalid | `results=[]`, structured warning. |
| Backend errors | Bubble up; FastMCP converts to tool error. |

## Testing

### Unit
- `test_tarjan.py`: empty graph, single self-loop, two-node cycle,
  three-node cycle, two disjoint cycles, mixed acyclic+cyclic.
- `test_mcp_tools_architecture.py`:
  - `find_cycles`: scope validation, provenance filter, `min_size`
    filter, `limit` clamping, output ordering.
  - `layering_violations`: empty layers, unmatched files counted,
    multi-match resolves to first layer, both edge kinds when
    `edge_kind="any"`, summary correct.
  - `hubs`: kind validation, `min_fanin` filter, `limit` clamping,
    ordering.

### Integration (`pytest.mark.integration`)
- `test_architecture_integration.py`: against `neo4j_backend`, build
  a tiny synthetic graph (3 files, 5 functions, one 2-cycle and one
  3-cycle, plus a violation across a known layering). Verify each
  tool returns the expected shape.

### MCP smoke
- Add the 3 names to `test_mcp_server_smoke.py`'s expected list.

## Performance

All three queries are bounded by project size:
- `find_cycles`: 1 read query for the full edge list of the relevant
  type, then in-memory Tarjan's (O(V+E)). Negligible for typical
  projects (<100k edges).
- `layering_violations`: 1 read for files + 1 read for edges + Python
  glob matching. 2 round-trips.
- `hubs`: 1 read query with `MATCH ... RETURN ... ORDER BY count(*)
  DESC LIMIT $limit`.

## Out of scope (future phases)

- Auto-derived layering suggestions.
- Cross-language analysis (waits on multi-language support).
- Edge-weighted cycle ranking (e.g. by call count). YAGNI for now.
- PageRank, betweenness centrality, community detection.
- Layering as a recurring CI check. Possible via `run_cypher` or a
  thin CLI wrapper later.
