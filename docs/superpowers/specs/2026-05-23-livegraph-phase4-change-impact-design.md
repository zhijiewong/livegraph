# livegraph Phase 4 — `change_impact` MCP Tool Design Specification

- **Date:** 2026-05-23
- **Status:** Approved (design); pending implementation plan
- **Scope of this spec:** One new MCP tool, `change_impact(diff, max_depth, provenance, limit)`, plus a unified-diff parser supporting it. Brings the MCP server total to 11 tools.
- **Out of scope (future):** NL→Cypher, embeddings / semantic search, write-mode tools, languages beyond Python, incremental graph updates, file-rename / deleted-file impact analysis.
- **Builds on:** Phase 1+2 graph (`docs/superpowers/specs/2026-05-23-livegraph-design.md`) and Phase 3 MCP server (`docs/superpowers/specs/2026-05-23-livegraph-phase3-mcp-design.md`).

---

## 1. Overview

`change_impact` is an 11th MCP tool that turns a unified git-diff string into actionable change-impact data: the symbols a change touches, the symbols those changes propagate to via `CALLS` edges, and the specific tests to run before merging.

Other tools (`git diff`, IDEs) report what *changed*. Static analyzers report what *might* be affected. `livegraph change_impact` answers the higher-leverage question — **what actually breaks if this ships, and which tests will catch it** — because the underlying graph has both static *and runtime-verified* `CALLS` edges plus per-test `COVERS` data. Neither is available to a static-only tool.

Phase 4 introduces no new infrastructure. It registers one new tool with the existing `livegraph mcp` FastMCP server, follows the established pure-function + thin-shim pattern, and reuses the Phase 1+2 graph as-is.

## 2. Rationale

The Phase 3 spec listed `change_impact(diff)` as a future-work item. The reasons it leads the Phase-4 candidate list:

- It is the most immediately useful agent-facing tool for real coding work: pre-commit risk analysis, PR review, regression-test selection.
- It depends on no new heavy infrastructure (no LLM, no model, no vector store) — it is purely a new query path through data we already have.
- It exercises the most differentiated parts of the graph (runtime-observed `CALLS` and per-test `COVERS`) — so its output is structurally something no static-only blast-radius tool can produce.

Risk scoring and natural-language summaries are deliberately deferred: this tool returns raw, transparent signals and trusts the caller (human or agent) to weight them.

## 3. Scope

**In scope:**

- A `livegraph/mcp/diff_parser.py` module with `parse_diff(diff_text)`.
- A `change_impact(backend, project, diff, max_depth, provenance, limit)` pure function in `livegraph/mcp/tools.py`.
- A FastMCP `@mcp.tool()` wrapper in `livegraph/mcp/server.py`.
- Unit, integration, and parser-fixture tests.
- A short tool-table update in `README.md`.

**Out of scope (future, separate specs):**

- A risk score / numeric "blast-radius rating".
- Tracking deleted files or renames as impact sources.
- A CLI subcommand (`livegraph impact …`) — MCP-only for v1.
- Parsing combined merge diffs (`@@@ ... @@@`).
- Binary-diff handling.
- The other Phase-3 future-work items (NL→Cypher, embeddings, incremental updates, multi-language).

## 4. Tech Stack

No new runtime dependencies. The diff parser is pure stdlib (string and `re`); the impact traversal uses Neo4j's variable-length path syntax against the existing graph; the MCP wrapper uses the already-installed `mcp>=1.10` SDK.

## 5. Architecture

```
livegraph/mcp/
  diff_parser.py    NEW — parse_diff(diff: str) -> dict[str, set[int]]
  tools.py          + change_impact() pure function
  server.py         + @mcp.tool() change_impact wrapper
```

**Data flow:**

```
unified diff text
   │
   ▼
diff_parser.parse_diff()        ← pure stdlib
   │
   ▼
{file_path → set[changed_lines]}
   │
   ▼   Query A: intersect with Function/Method line spans, get changed symbols
   ▼   Query B: traverse CALLS upstream (depth-bounded) -> impacted symbols
   ▼   Query C: traverse COVERS from (changed ∪ impacted) -> tests to run
   │
   ▼
{ changed, impacted, tests_to_run, unmatched_files, stats }
```

**Cross-cutting decisions:**

- **Direction:** upstream — callers of changed symbols are impacted.
- **Depth:** parameter `max_depth: int = 5`. Variable-length Cypher `[:CALLS*1..N]` with `N` safely interpolated (`max_depth` is validated by FastMCP and clamped to `1..20` in the tool).
- **Provenance:** `provenance: "any" | "static" | "runtime"`, default `"any"`. Filters edges in the path (`all(rel IN relationships(path) WHERE …)`).
- **Coarse-grained line attribution:** a symbol is "changed" if its `[start_line, end_line]` overlaps any touched line in its file. Conservative by design (false positives are cheaper than false negatives for impact analysis).
- **Project scoping:** all three queries traverse through the configured project's `CONTAINS` chain so cross-project node-sharing (a Phase 1 limitation) does not leak.
- **Limits:** the `limit` parameter applies to the `impacted` list only — the largest in worst cases. `changed` and `tests_to_run` are derived sets bounded by inputs.

## 6. Diff Parsing

`parse_diff(diff_text: str) -> dict[str, set[int]]` returns a map from project-relative file path to the set of new-file line numbers touched by the diff.

### Algorithm

1. Scan for `+++ b/<path>` lines — the path of the file being modified, with the `b/` prefix stripped and separators normalized to forward slash.
2. Skip blocks where the `+++` line is `/dev/null` (the file was deleted).
3. For each `@@ -A,B +P,Q @@` hunk header, capture `P` (new-file start line) and walk the hunk body maintaining `current_new_line = P`:
   - line beginning with `+` → mark `current_new_line` as changed, then increment
   - line beginning with ` ` (context) → increment without marking
   - line beginning with `-` → do not change `current_new_line` (no new-file line)
4. Ignore unrecognized lines (e.g. `\ No newline at end of file`, mode-change lines, `index …` headers). Never raise on malformed input.

### Edge cases (explicit)

- **New file added** (`--- /dev/null`): the `+++ b/<path>` is captured and every `+` line in the hunk is marked. The intersect step (§7) will report no changed symbols if the file is not yet ingested in the graph.
- **Deleted file** (`+++ /dev/null`): the block is skipped. The old file path is *not* tracked; deletions are a documented v1 limitation.
- **Binary diff** (`Binary files X and Y differ`): the block has no hunks; produces no entries for that file.
- **Empty diff** (no `+++` lines): returns `{}`.
- **Combined diff** (merge diffs with `@@@ -A,B -C,D +P,Q @@@`): not supported in v1; lines are skipped silently.

### Output shape

`{ "livegraph/foo.py": {13, 14, 15, 16}, "livegraph/bar.py": {42} }` — keys are forward-slash, project-relative paths; values are integer sets of new-file line numbers.

## 7. Impact Traversal & Output

Three Cypher queries run back-to-back, all project-scoped.

### Query A — changed symbols

```cypher
UNWIND $files AS spec
MATCH (:Project {name: $project})-[:CONTAINS]->(file:File {path: spec.path})
MATCH (file)-[:DEFINES|HAS_METHOD*1..2]->(s)
WHERE (s:Function OR s:Method)
  AND any(line IN spec.lines WHERE
          line >= s.start_line AND line <= s.end_line)
RETURN DISTINCT s.qualified_name AS qualified_name, s.name AS name,
       head([l IN labels(s) WHERE l IN ['Function','Method','Class']
             | toLower(l)]) AS kind,
       s.file AS file, s.start_line AS start_line, s.end_line AS end_line,
       coalesce(s.runtime_observed, false) AS runtime_observed,
       coalesce(s.coverage_pct, 0.0)       AS coverage_pct
```

`$files` is `[{"path": "foo.py", "lines": [13, 14, …]}, …]`. Files in the parsed diff that yield no matching `File` node in the project are collected as `unmatched_files` and returned in the response.

### Query B — impacted callers (transitive)

`max_depth` is interpolated into the query string (validated int, clamped to `1..20`).

```cypher
UNWIND $changed_qns AS changed_qn
MATCH (changed {qualified_name: changed_qn})
MATCH (:Project {name: $project})-[:CONTAINS]->(:File)
      -[:DEFINES|HAS_METHOD*1..2]->(impacted)
MATCH path = (impacted)-[:CALLS*1..{MAX_DEPTH}]->(changed)
WHERE all(rel IN relationships(path) WHERE
          ($provenance = 'any')
       OR ($provenance = 'static'  AND rel.static  = true)
       OR ($provenance = 'runtime' AND rel.runtime = true))
WITH impacted, changed_qn, length(path) AS depth,
     [r IN relationships(path) | {static: coalesce(r.static, false),
                                  runtime: coalesce(r.runtime, false)}]
       AS edge_provenance
RETURN impacted.qualified_name AS qualified_name, impacted.name AS name,
       head([l IN labels(impacted) WHERE l IN ['Function','Method','Class']
             | toLower(l)]) AS kind,
       impacted.file AS file, impacted.start_line AS start_line,
       impacted.end_line AS end_line,
       coalesce(impacted.runtime_observed, false) AS runtime_observed,
       coalesce(impacted.coverage_pct, 0.0)       AS coverage_pct,
       collect(DISTINCT {via: changed_qn, depth: depth,
                         edges: edge_provenance})    AS reached_via
ORDER BY qualified_name
LIMIT $limit
```

### Query C — tests to run

```cypher
UNWIND $all_affected_qns AS qn
MATCH (s {qualified_name: qn})
MATCH (t:Test)-[c:COVERS]->(s)
RETURN DISTINCT t.qualified_name AS qualified_name, t.name AS name,
       head([l IN labels(t) WHERE l IN ['Function','Method','Class']
             | toLower(l)]) AS kind,
       t.file AS file, t.start_line AS start_line, t.end_line AS end_line,
       coalesce(t.test_outcome, '') AS test_outcome,
       collect(DISTINCT qn)                       AS covers_symbols,
       avg(coalesce(c.coverage_pct, 0.0))         AS avg_coverage_pct
ORDER BY qualified_name
```

`$all_affected_qns` is the union of changed and impacted `qualified_name`s.

### Tool signature & return shape

```python
change_impact(diff: str, max_depth: int = 5,
              provenance: "any" | "static" | "runtime" = "any",
              limit: int = 200) -> dict
```

```python
{
  "changed":      [ SymbolRef + {runtime_observed, coverage_pct} ],
  "impacted":     [ SymbolRef + {
                      runtime_observed, coverage_pct,
                      reached_via: [ {via: qualified_name, depth: int,
                                      edges: [ {static, runtime} ]} ]
                  } ],
  "tests_to_run": [ SymbolRef + {
                      test_outcome,
                      covers_symbols: [qualified_name],
                      avg_coverage_pct: float
                  } ],
  "unmatched_files": [ "path/never/ingested.py", … ],
  "stats": {
      "changed_files":     int,
      "changed_symbols":   int,
      "impacted_symbols":  int,
      "tests_to_run":      int,
      "max_depth_reached": int,
  }
}
```

`reached_via` is the agent's main "why" signal: it shows the chain of changed symbols that propagate to each impacted symbol, with depth and per-edge provenance. An all-static long path is low confidence; a short all-runtime path is high confidence. The agent decides.

## 8. MCP Wiring

One new entry in `build_server()`:

```python
@mcp.tool()
def change_impact(diff: str, max_depth: int = 5,
                  provenance: str = "any",
                  limit: int = 200) -> dict[str, Any]:
    """Given a unified diff, return changed/impacted symbols and tests to run."""
    backend, project = _require_state()
    return tools.change_impact(backend, project, diff=diff,
                               max_depth=max_depth, provenance=provenance,
                               limit=limit)
```

No CLI subcommand. No new env vars. No new dependencies.

## 9. Error Handling

| Failure | Behavior |
|---|---|
| Empty diff (no `+++` lines) | Return all-empty result; `stats.changed_files = 0`. No error. |
| Malformed / partially-readable diff | Tolerant parser recovers what it can; unrecognized lines skipped silently. |
| File in diff is not ingested (no File node) | Listed in `unmatched_files`. Other files in the diff still processed. |
| Changed symbol with no callers in graph | Just appears in `changed` with empty contribution to `impacted`. No error. |
| `max_depth` out of `[1, 20]` | Clamped to that range by the tool before query construction. |
| `provenance` not in {`any`, `static`, `runtime`} | FastMCP raises a structured validation error before the tool runs. |
| Neo4j unreachable | Surfaced as the same MCP error any other tool would produce. |

## 10. Testing Strategy

**Unit (no Neo4j):**

- `tests/unit/test_diff_parser.py` — fixture diffs covering single-file modify, multi-hunk modify, multi-file diff, new-file add, deleted file, binary diff, empty diff. Each asserts on the exact `{file: set[lines]}` output. ~8–10 tests.
- `tests/unit/test_mcp_tools_change_impact.py` — `change_impact` against `FakeBackend` with canned rows for Queries A/B/C. Verifies output assembly, `unmatched_files` handling, `stats` math, `max_depth` clamping, parameter passthrough. ~5–6 tests.

**Integration (real Neo4j, reuses Phase 3's `ingested_sample` fixture):**

- `tests/integration/test_change_impact_integration.py` — three scenarios:
  1. **Diff that changes `Calculator.add`** → asserts `runner.py::run_operation` shows up in `impacted` with `reached_via[0].depth == 1` and at least one edge with `runtime == True` (the dynamic-dispatch edge surfacing through Phase 4).
  2. **Diff that touches a file the test suite doesn't cover** → asserts the changed symbol appears with `coverage_pct == 0.0` and `runtime_observed == False`.
  3. **Diff against a file not in the graph** → asserts the file shows up in `unmatched_files` and the rest of the response is empty / well-formed.

The Phase-4 acceptance test is scenario #1: it confirms the agent sees the runtime-tracked dynamic-dispatch caller, with provenance, through `change_impact`. That output is something no static-only blast-radius tool can produce.

## 11. Repo Layout After Phase 4

```
livegraph/
  mcp/
    diff_parser.py                    NEW
    tools.py                          + change_impact()
    server.py                         + @mcp.tool() change_impact
tests/
  unit/
    test_diff_parser.py               NEW (~10 tests)
    test_mcp_tools_change_impact.py   NEW (~6 tests)
  integration/
    test_change_impact_integration.py NEW (~3 tests)
README.md                             updated tool table
```

## 12. Risks

| Risk | Mitigation |
|---|---|
| Variable-length path queries (`[:CALLS*1..N]`) are expensive on large/cyclic graphs | Default `max_depth=5`; clamped to `1..20`; `limit` caps `impacted`; cost documented in the tool docstring. |
| Conservative line-overlap reports a function as "changed" when only a comment in its body was edited | Documented expected behavior; honest false-positive bias preferred to false-negatives for impact analysis. |
| Deleted-file and rename-file impact is invisible | Documented v1 limitation; `unmatched_files` flags files the tool couldn't reason about. |
| Diff parser fooled by adversarial / non-`git` inputs | Tolerant parser (never raises, skips unrecognized lines); fuzz-style coverage in fixtures. |
| Cypher-injection through `max_depth` int interpolation | `max_depth` is FastMCP-validated as int, clamped to `1..20` defensively in the tool before the query is built. No string input is ever interpolated into Cypher. |
| Phase 1 cross-project node-sharing leaks results from another project | All three queries traverse through `(:Project {name: $project})` so impacts and tests stay scoped to the configured project — same defensive pattern Phase 3's reviewer fix established. |

## 13. Future Work (out of scope)

- A `risk_score` field derived from a transparent formula over the raw signals.
- File-rename / deleted-file impact modeling (likely needs old-graph snapshots).
- A CLI subcommand `livegraph impact <diff-file>` for pre-commit hooks.
- An "explain" mode that walks the longest `reached_via` chain in natural language.
- Per-test cost / runtime data so `tests_to_run` can be ordered for minimum total wall-clock.
