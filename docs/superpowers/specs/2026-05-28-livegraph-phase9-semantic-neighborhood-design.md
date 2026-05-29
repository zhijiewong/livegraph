# livegraph Phase 9 — `semantic_neighborhood` (design)

**Date:** 2026-05-28
**Status:** Approved

## Goal

Add a 15th MCP tool `semantic_neighborhood(query, ...)` that fuses Phase 7's
vector search with Phase 1/2's call/coverage graph in a single call: for each
semantic seed, return its direct callers, callees, and tests. This is the
"where do I look, what do I run" question an agent really wants when starting
from a natural-language concept.

## Non-goals

- Multi-hop graph expansion (`find_callers`/`run_cypher` cover that).
- Source previews inline (the agent calls `get_source` on chosen symbols).
- New MCP tools beyond `semantic_neighborhood`.
- Touching `semantic_search` — it stays as the pure-vector tool.

## Tool surface

```
semantic_neighborhood(
    query: str,
    limit: int = 10,            # number of semantic seeds, max 50
    per_seed_limit: int = 10,   # cap per expansion list, max 50
    kind: str = "any",          # "any" | "function" | "method"
    include: list[str] | None = None,   # subset of {"callers","callees","tests"}
    min_score: float = 0.0,
) -> dict
```

`include=None` means all three (`["callers", "callees", "tests"]`).

### Response shape

```json
{
  "model": "all-MiniLM-L6-v2",
  "embedded_count": 412,
  "results": [
    {
      "qualified_name": "myproj.calc.Calculator.add",
      "kind": "method",
      "score": 0.78,
      "file": "myproj/calc.py",
      "line": 14,
      "callers": [
        {"qualified_name": "myproj.api.handle_add", "provenance": "runtime"}
      ],
      "callees": [
        {"qualified_name": "builtins.int", "provenance": "static"}
      ],
      "tests": [
        {"qualified_name": "tests.test_calc.test_add_positive"}
      ]
    }
  ],
  "warning": null
}
```

- `score` is cosine similarity (`0..1`), matching `semantic_search`.
- `provenance` on callers/callees mirrors `find_callers`/`find_callees`:
  `"static" | "runtime" | "both"`.
- `tests` entries carry only `qualified_name` (matches existing `tests_for`).
- If `include` omits a list, that field is absent from each result (not an
  empty list, so the wire format stays small).
- Same graceful-degradation surface as `semantic_search`: when `[semantic]`
  isn't installed or the vector index is missing/empty, `results=[]` and
  `warning` carries the actionable hint. `embedded_count` is `0` in that
  case.

## Architecture

New file: `livegraph/mcp/tools_neighborhood.py` — keeps the existing
`livegraph/mcp/tools.py` from growing further (it's already ~1000 lines).
Exports a single function `semantic_neighborhood(...)` that:

1. **Param validation** — clamp `limit` and `per_seed_limit` to `[1, 50]`;
   normalize `include` to the allowed subset (unknowns dropped with a
   `warning` line); reject `kind` not in `{any, function, method}` with a
   structured warning + `results=[]`.
2. **Seed step** — same code path as `semantic_search`: encode `query` via
   the provider, run `CALL db.index.vector.queryNodes(...)` with `limit*2`
   and filter to project + kind + `min_score`, truncate to `limit`.
3. **Expansion step** — three Cypher queries, each parameterized by
   `$qns: list[str]` and `$per_seed_limit: int`, returning rows shaped as
   `(seed_qn, neighbor_qn, [provenance])`. The queries run only for
   expansion types included in `include` (so an agent asking for callers
   only pays for one expansion query, not three).
4. **Join** — Python-side groups expansion rows by `seed_qn` into the
   per-result `callers`/`callees`/`tests` lists. Order within each list is
   stable (Cypher `ORDER BY` ensures determinism).
5. **Return** — assemble the response.

The seed step reuses the helper from `semantic_search` rather than copying
it. To keep both tools symmetric, extract the encode-and-query block into a
small shared helper `_semantic_seeds(backend, project, provider, query,
limit, kind, min_score)` in `tools.py` and call it from both places.

## Cypher

### Callers expansion (similar shape for callees)

```cypher
UNWIND $qns AS qn
MATCH (target {qualified_name: qn})
MATCH (caller)-[r:CALLS]->(target)
WITH qn, caller, collect(DISTINCT r.provenance) AS provs
ORDER BY caller.qualified_name
WITH qn, caller.qualified_name AS neighbor_qn,
     CASE
       WHEN size(provs) > 1 THEN 'both'
       ELSE provs[0]
     END AS provenance
WITH qn, collect({neighbor_qn: neighbor_qn, provenance: provenance})[..$per_seed_limit] AS rows
UNWIND rows AS row
RETURN qn AS seed_qn, row.neighbor_qn AS neighbor_qn, row.provenance AS provenance
```

### Tests expansion

```cypher
UNWIND $qns AS qn
MATCH (target {qualified_name: qn})
MATCH (test:Test)-[:COVERS]->(target)
WITH qn, test.qualified_name AS neighbor_qn
ORDER BY neighbor_qn
WITH qn, collect(neighbor_qn)[..$per_seed_limit] AS rows
UNWIND rows AS neighbor_qn
RETURN qn AS seed_qn, neighbor_qn
```

(Existing `tests_for` and `find_callers` queries are similar — these
adapt them to the `UNWIND $qns` batched form, removing the N+1 round
trips that the naive implementation would have.)

## File map

| File | Action | Responsibility |
|---|---|---|
| `livegraph/mcp/tools_neighborhood.py` | Create | `semantic_neighborhood(...)` + the 3 expansion Cypher queries. |
| `livegraph/mcp/tools.py` | Modify | Extract `_semantic_seeds(...)` helper; have `semantic_search` call it. |
| `livegraph/mcp/server.py` | Modify | Register `semantic_neighborhood` as the 15th tool. Update docstring "14 tools" → "15 tools". |
| `tests/unit/test_mcp_tools_semantic_neighborhood.py` | Create | Behavior tests with a fake backend + fake provider. |
| `tests/integration/test_semantic_neighborhood_integration.py` | Create | Real Neo4j + real MiniLM acceptance: query → seed → expand → return. |
| `tests/integration/test_mcp_server_smoke.py` | Modify | Add `"semantic_neighborhood"` to the registered-tools assertion. |
| `README.md` | Modify | Add "Semantic neighborhood" section. |

## Error handling

| Source | Behavior |
|---|---|
| `[semantic]` extra missing | `results=[]`, `warning="install livegraph[semantic] ..."`, exit gracefully. Same path as `semantic_search`. |
| No vector index (empty embeddings) | `results=[]`, `warning="no embeddings yet — run livegraph embed"`. Same path as `semantic_search`. |
| Invalid `kind` | `results=[]`, structured `warning` listing valid values. Same path as `semantic_search`. |
| Empty `include` (after normalization) | Treated as `["callers","callees","tests"]` (the default) and a `warning` flags it. |
| Backend errors | Bubble up; FastMCP converts to a tool error. |

## Testing

### Unit (`tests/unit/test_mcp_tools_semantic_neighborhood.py`)

- Param clamping (`limit`, `per_seed_limit`).
- `kind` validation surfaces a warning, no DB calls.
- Missing `[semantic]` → graceful `results=[]` + warning, no DB calls.
- Vector seeds + expansion rows correctly grouped into per-seed lists.
- `include=["callers"]` only runs the callers expansion query; `callees`
  and `tests` fields absent on results.
- Provenance collapse: if a `(caller, target)` pair has both static and
  runtime CALLS edges, the response carries `provenance: "both"`.
- `min_score` filter drops seeds below threshold.

Uses the existing `_QueuedBackend`-style fake and a `FakeProvider` that
returns canned vectors.

### Integration (`tests/integration/test_semantic_neighborhood_integration.py`)

- `semantic_neighborhood("addition arithmetic", limit=3)` against the
  sample project returns `Calculator.add` in top-3 (same acceptance as
  Phase 7) AND attaches at least one caller and one test to it.
- `include=["callers"]` exercises the include-subset code path against
  real Neo4j.

### MCP smoke

Extend `tests/integration/test_mcp_server_smoke.py` so the
`sorted([...])` list includes `"semantic_neighborhood"`.

## Performance

Per call: **1 vector query + ≤3 expansion queries** (one per requested
include). With default `limit=10`, `per_seed_limit=10`, three expansions,
that's 4 round trips regardless of seed count — same shape as Phase 6's
`run_cypher`. Bench target: <500ms warm against the sample project.

## Out of scope (future phases)

- Multi-hop expansion (`depth>1`) — use `find_callers`/`run_cypher`.
- Source previews / coverage stats inline — call `get_source`.
- A `--rerank` step that re-scores neighbors by graph centrality — could
  be Phase 10 if we see agents want it.
