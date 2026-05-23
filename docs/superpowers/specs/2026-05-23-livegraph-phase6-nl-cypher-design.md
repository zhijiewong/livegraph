# livegraph Phase 6 — NL→Cypher Design Specification

- **Date:** 2026-05-23
- **Status:** Approved (design); pending implementation plan
- **Scope of this spec:** Two new MCP tools — `describe_schema()` and `run_cypher(query, params, row_limit, timeout_seconds)` — that let a coding agent's own LLM generate and run arbitrary read-only Cypher against the livegraph graph. No LLM dependency inside livegraph. Brings the server total from 11 to 13 tools.
- **Out of scope (future):** `livegraph query` CLI subcommand, procedure allowlist (CALL is blanket-banned), pagination/cursor APIs, write-mode tools (ever), `EXPLAIN`-plan inspection, embeddings/semantic search, multi-language, `livegraph watch` daemon.
- **Builds on:** Phase 1+2 (graph), Phase 3 (MCP server, 10 tools), Phase 4 (`change_impact`, tool 11), Phase 5 (incremental updates).

---

## 1. Overview

Phase 6 adds the agent-facing "ask anything" escape hatch that the prior phases deliberately deferred. Two MCP tools — `describe_schema()` and `run_cypher(query, params)` — let an agent's own LLM compose and run read-only Cypher against the livegraph graph.

The Phase 1+2 research warned that NL→Cypher is the feature that **hurts** comparable tools' accuracy: code-graph-rag stuffs its own LLM in the middle, which then becomes the bottleneck. Phase 6 sidesteps that by keeping the LLM out of livegraph entirely. The host agent's LLM — Claude Sonnet/Opus, GPT-5, whatever — is by definition the most capable model available at query time. We pass it the schema, it writes the Cypher, we validate and run it. **No model selection, no API key, no provider configuration inside livegraph.**

This is the MCP-native idiom: expose primitives, let the host agent compose.

## 2. Rationale

Every prior phase added structured tools — `find_callers`, `change_impact`, `runtime_only_calls`, and so on — that answer specific, anticipated questions. Phase 6 closes a different gap: the questions we *didn't* anticipate.

The 11 existing tools cover the 80% of common code-graph questions. The remaining 20% — "show me functions that have no tests and are called by something runtime-only", "list every module imported by both file A and file B", "find decorators that appear on methods of more than one class" — require composing Cypher. With Phase 6, the agent composes that Cypher itself; with anything else, those questions would require either a new specific tool (overfitting the 13-tool surface) or a brittle in-server LLM (the code-graph-rag failure mode).

Safety is non-negotiable. `run_cypher` enforces read-only via belt-and-suspenders: a lexical pre-scan for forbidden keywords, plus a Neo4j read transaction at the engine level. The lexer catches mistakes with friendly messages; the read transaction enforces correctness even if the lexer is fooled.

## 3. Scope

**In scope:**

- A new `livegraph/mcp/cypher_guard.py` module with `validate(query)`, `auto_limit(query, row_limit)`, and the execution wrapper.
- Two new pure-function tools in `livegraph/mcp/tools.py`: `describe_schema(backend, project, neo4j_version)` and `run_cypher(backend, project, query, params, row_limit, timeout_seconds)`.
- Two new FastMCP wrappers in `livegraph/mcp/server.py`. Brings the registered tool count from 11 → 13.
- Two new `Settings` fields: `livegraph_query_row_limit: int = 1000`, `livegraph_query_timeout_seconds: int = 30`.
- Unit, integration, and live-Neo4j tests covering the validation pipeline, schema-introspection round-trip, dynamic-dispatch example, timeout, truncation, and engine-level write rejection.
- A README section on agent-side Cypher composition.

**Out of scope (future, separate specs):**

- A `livegraph query "MATCH ..."` CLI subcommand.
- Procedure allowlist (every `CALL ...` is blanket-banned in v1).
- Cypher EXPLAIN-plan inspection.
- Result pagination or cursor APIs (agents paginate by tightening predicates).
- Write-mode tools (livegraph remains read-only as a principle).
- Embeddings / semantic search.
- Multi-language support.

## 4. Tech Stack

No new runtime dependencies. The validation logic is pure Python `re`. The Cypher execution uses the existing `neo4j` driver's `session(default_access_mode="READ")` API with `execute_read` transaction callbacks for engine-level write rejection and per-transaction timeouts.

## 5. Architecture

```
livegraph/mcp/
  cypher_guard.py    NEW — validate(query) + auto_limit + read-mode runner
  tools.py           + describe_schema(), run_cypher()
  server.py          + 2 new @mcp.tool() wrappers (tool 12 + 13)
livegraph/config.py  + livegraph_query_row_limit, livegraph_query_timeout_seconds
```

### Data flow for `run_cypher`

```
agent → run_cypher(query, params)
   │
   ▼
1. Lexical pre-scan: reject if query matches forbidden-keyword pattern
                     (CREATE, MERGE, DELETE, DETACH DELETE, SET, REMOVE,
                      DROP, LOAD CSV, USING PERIODIC COMMIT, CALL)
   │
   ▼
2. Inject $project = configured project name into params (setdefault — caller
                     can override by passing an explicit value)
   │
   ▼
3. Auto-append "LIMIT $row_limit" if query has no trailing LIMIT clause
   │
   ▼
4. Open a Neo4j READ transaction (engine refuses any write that the
                     lexer missed)
   │
   ▼
5. Run with server-side transaction timeout
   │
   ▼
6. Truncate at row_limit, set truncated: bool
   │
   ▼
{ rows, truncated, row_count, summary }
```

### Project-scoping convention

`describe_schema` advertises the configured project name and a `safety.convention` string saying "every query should scope through `(:Project {name: $project})-[:CONTAINS]->...`". The agent uses `$project` symbolically. `run_cypher` injects the actual project name into `params["project"]` automatically (only if the caller didn't override). Result: agents write idiomatic project-scoped Cypher without needing to remember the project name; explicit override is still possible.

## 6. `describe_schema()` Tool

### Signature

```python
describe_schema() -> dict
```

No arguments.

### Return shape

```python
{
  "project": "sample",
  "neo4j_version": "5.26",

  "node_labels": {
      "Project":  { "key": "name",
                    "properties": ["name", "root_path"] },
      "File":     { "key": "path",
                    "properties": ["path", "name", "language",
                                   "parse_error", "content_hash"] },
      "Class":    { "key": "qualified_name",
                    "properties": ["qualified_name", "name", "file",
                                   "start_line", "end_line",
                                   "decorators", "source"] },
      "Function": { "key": "qualified_name",
                    "properties": ["qualified_name", "name", "file",
                                   "start_line", "end_line",
                                   "decorators", "source",
                                   "runtime_observed", "coverage_pct",
                                   "runtime_stale",
                                   "test_outcome", "test_duration"] },
      "Method":   { "key": "qualified_name",
                    "properties": ["qualified_name", "name", "file",
                                   "start_line", "end_line",
                                   "decorators", "source",
                                   "runtime_observed", "coverage_pct",
                                   "runtime_stale"] },
      "Test":     { "note": "An additional label on Function nodes "
                            "(test functions covered by livegraph trace). "
                            "Test nodes also satisfy :Function." },
      "Module":   { "key": "name",
                    "properties": ["name", "kind"] },
  },

  "edge_types": {
      "CONTAINS":   { "from": "Project|File", "to": "File",
                      "properties": [] },
      "DEFINES":    { "from": "File", "to": "Class|Function",
                      "properties": [] },
      "HAS_METHOD": { "from": "Class", "to": "Method",
                      "properties": [] },
      "IMPORTS":    { "from": "File", "to": "File|Module",
                      "properties": ["raw", "line"] },
      "CALLS":      { "from": "Function|Method", "to": "Function|Method",
                      "properties": ["static", "runtime",
                                     "observed_count", "call_site_lines"],
                      "note": "Provenance flags: c.static=true means AST "
                              "predicted the call; c.runtime=true means it "
                              "was observed executing. "
                              "(static=false, runtime=true) is the "
                              "dynamic-dispatch differentiator." },
      "COVERS":     { "from": "Test", "to": "Function|Method",
                      "properties": ["lines_covered", "lines_total",
                                     "coverage_pct"] },
  },

  "safety": {
      "read_only": True,
      "forbidden_keywords": ["CREATE", "MERGE", "DELETE", "DETACH DELETE",
                             "SET", "REMOVE", "DROP", "LOAD CSV",
                             "USING PERIODIC COMMIT", "CALL"],
      "row_limit_default": 1000,
      "timeout_seconds_default": 30,
      "project_auto_injected": True,
      "convention": "Every query should scope through "
                    "(:Project {name: $project})-[:CONTAINS]->(:File)->... ; "
                    "the $project parameter is injected automatically.",
  },

  "example_queries": [
      {
          "intent": "Find a symbol by name",
          "query": "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
                   "-[:DEFINES|HAS_METHOD*1..2]->(s) "
                   "WHERE toLower(s.name) CONTAINS toLower($q) "
                   "RETURN s.qualified_name, s.name, labels(s), "
                   "       s.file, s.start_line "
                   "LIMIT 20",
          "params_hint": {"q": "<search term>"},
      },
      {
          "intent": "Find who calls a symbol",
          "query": "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
                   "-[:DEFINES|HAS_METHOD*1..2]->(callee) "
                   "WHERE callee.qualified_name = $qn "
                   "MATCH (caller)-[c:CALLS]->(callee) "
                   "RETURN caller.qualified_name, c.static, c.runtime, "
                   "       c.observed_count",
          "params_hint": {"qn": "<qualified_name>"},
      },
      {
          "intent": "Dynamic-dispatch calls — runtime caught what static missed",
          "query": "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
                   "-[:DEFINES|HAS_METHOD*1..2]->(caller)"
                   "-[c:CALLS]->(callee) "
                   "WHERE c.runtime = true "
                   "  AND coalesce(c.static, false) = false "
                   "RETURN caller.qualified_name, callee.qualified_name, "
                   "       c.observed_count "
                   "LIMIT 50",
          "params_hint": {},
      },
      {
          "intent": "Tests that cover a symbol",
          "query": "MATCH (s {qualified_name: $qn}) "
                   "MATCH (t:Test)-[c:COVERS]->(s) "
                   "RETURN t.qualified_name, c.coverage_pct, "
                   "       c.lines_covered, c.lines_total "
                   "ORDER BY c.coverage_pct DESC",
          "params_hint": {"qn": "<qualified_name>"},
      },
      {
          "intent": "Untested functions/methods",
          "query": "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
                   "-[:DEFINES|HAS_METHOD*1..2]->(s) "
                   "WHERE (s:Function OR s:Method) AND NOT s:Test "
                   "  AND coalesce(s.runtime_observed, false) = false "
                   "RETURN s.qualified_name, s.file "
                   "LIMIT 100",
          "params_hint": {},
      },
      {
          "intent": "Files that import a given file",
          "query": "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
                   "-[r:IMPORTS]->(dst:File {path: $file}) "
                   "RETURN src.path, r.raw, r.line "
                   "ORDER BY src.path",
          "params_hint": {"file": "<relative path>"},
      },
  ],
}
```

### Why this shape

- **`safety` is in the response.** Agent reads it once and knows the rules before composing.
- **`example_queries` is the killer feature.** Six examples teach the agent the *idioms* of this graph: project scoping, the `DEFINES|HAS_METHOD*1..2` label-routing pattern, provenance predicates, `coalesce` on nullable properties. With these, an LLM writes idiomatic Cypher; without them, it invents wrong joins.
- **The `Test` "note"** prevents a common confusion — Test isn't a separate node kind, it's a *secondary label* on certain Function nodes.
- **`runtime_stale`, `coverage_pct`, `runtime_observed`** are surfaced as properties. This is the first place in the MCP tool surface that those Phase 2/5 properties get explicit treatment — the existing 11 tools store them but most don't surface them in their RETURN clauses yet.

### What `describe_schema` does NOT return

- Sample data values (no row dumps; agent doesn't need them — examples carry semantics).
- Per-node counts (`graph_status` already exists for that).
- The Cypher full grammar (the agent's LLM already knows it).
- Internal node IDs (`elementId`, `id`) — irrelevant to graph queries.

## 7. `run_cypher` Tool

### Signature

```python
run_cypher(query: str,
           params: dict[str, Any] | None = None,
           row_limit: int = 1000,
           timeout_seconds: int = 30) -> dict
```

### Pipeline (in order)

1. **Lexical pre-scan**. Reject early with friendly message if `query` matches the forbidden-keyword regex (see below).
2. **Inject `$project`**. `caller_params.setdefault("project", configured_project_name)`. Caller can override by passing an explicit `project=...` in `params`.
3. **Auto-append LIMIT**. If `re.search(r"\bLIMIT\b\s+\d+\s*$", query, re.IGNORECASE)` is None, append ` LIMIT {row_limit}` to the end of the query (after stripping any trailing semicolon).
4. **Open a Neo4j READ transaction.** `default_access_mode="READ"` is engine-enforced — any write clause that bypassed step 1 fails here.
5. **Run with transaction timeout.** Per-transaction `timeout=timedelta(seconds=timeout_seconds)`. The Neo4j server kills the query if it exceeds the deadline.
6. **Truncate and return.** Convert records to plain dicts; if `len(records) > row_limit` truncate and set `truncated: true`.

### The forbidden-keyword regex

```python
_FORBIDDEN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|DROP|"
    r"LOAD\s+CSV|USING\s+PERIODIC\s+COMMIT|CALL)\b",
    re.IGNORECASE,
)
```

Case-insensitive, word-boundary-anchored. Multi-word forms (`LOAD CSV`, `USING PERIODIC COMMIT`, `DETACH DELETE`) tolerate whitespace.

Rejects: every write clause (`CREATE`, `MERGE`, `DELETE`, `SET`, `REMOVE`), schema management (`DROP`), bulk loading (`LOAD CSV`, `USING PERIODIC COMMIT`), and any procedure call (`CALL`). The blanket `CALL` ban is a deliberate v1 default — it locks out useful read procedures like `db.labels()` in exchange for not maintaining a procedure allowlist. `describe_schema` already exposes the same information statically.

A query whose string literal contains a forbidden keyword (e.g. `MATCH (n) WHERE n.body CONTAINS "CREATE"`) will be rejected by the lexer. The agent can re-issue with the constant parameterized (`WHERE n.body CONTAINS $needle`). Documented in error messages.

### Return shape

```python
{
  "rows": [ { ... per-row dict ... }, ... ],   # at most row_limit rows
  "truncated": bool,                            # True if rows were cut off
  "row_count": int,                             # len(rows)
  "summary": {
      "available_after_ms": int,
      "consumed_after_ms": int,
      "query_type": "read",
  },
}
```

### Error responses (structured MCP errors)

| Error | Triggered when |
|---|---|
| `forbidden_keyword: <kw>` | Step 1 catches a write clause or `CALL` |
| `cypher_syntax: <neo4j message>` | The driver rejects the syntax |
| `engine_write_attempted` | Step 4 blocks something step 1 missed (defense in depth) |
| `timeout: query exceeded <N>s` | Step 5 timeout fires |
| `query_too_complex` | Neo4j raises a complexity error |

Each error includes the original query so the agent can correct it.

## 8. CLI, Configuration & Error Handling

### CLI

No new top-level command. Both tools are MCP-only. A future `livegraph query` for local testing is explicitly deferred.

### Configuration

Two new `Settings` fields (with env-var overrides):

```python
livegraph_query_row_limit: int = 1000              # LIVEGRAPH_QUERY_ROW_LIMIT
livegraph_query_timeout_seconds: int = 30          # LIVEGRAPH_QUERY_TIMEOUT_SECONDS
```

Per-call `run_cypher` arguments override these. The MCP server reads `Settings` at bootstrap and passes them as defaults to the tool wrapper.

No new credentials, no provider config — the "no LLM inside livegraph" stance from §1 means zero new keys.

### Error handling (consolidated)

| Failure | Tool response | Server behavior |
|---|---|---|
| Forbidden keyword in query | MCP error `forbidden_keyword: <kw>` + original query | Server stays up |
| Cypher syntax error | MCP error `cypher_syntax: <neo4j message>` + query | Server stays up |
| Engine refuses a write (read-tx) | MCP error `engine_write_attempted` + query | Server stays up |
| Timeout | MCP error `timeout: query exceeded <N>s` + query | Server stays up; Neo4j rolls back the transaction |
| Result exceeds `row_limit` | Success: truncate, set `truncated: true` | Normal |
| Configured project not in graph | `describe_schema.safety.convention` includes the same warning Phase 3's `_warn_if_project_missing` emits | Server stays up |
| Neo4j unreachable during a tool call | Standard connectivity MCP error | Same as other tools |

## 9. Testing Strategy

**Unit (no Neo4j):**

- `tests/unit/test_cypher_guard.py` — the safety pipeline. ~12 tests:
  - Each forbidden keyword rejected (CREATE, MERGE, DELETE, DETACH DELETE, SET, REMOVE, DROP, LOAD CSV, USING PERIODIC COMMIT, CALL — 10 cases).
  - Case-insensitivity (`create`, `Create`, `CREATE` all rejected).
  - Word-boundary correctness.
  - `$project` auto-injection when omitted; explicit override honored.
  - Auto-LIMIT appended when missing; not appended when present.
  - Trailing semicolon stripped before LIMIT append.
- `tests/unit/test_mcp_tools_query.py` — tool output shapes via `FakeBackend`. ~6 tests:
  - `describe_schema` returns the documented keys + configured project name.
  - `describe_schema.example_queries` includes all six advertised intents.
  - `run_cypher` returns `{rows, truncated, row_count, summary}`.
  - `run_cypher` error response for each error class.

**Integration (real Neo4j, reuses Phase 3's `ingested_sample` fixture):**

- `tests/integration/test_cypher_query_integration.py` — ~6 tests:
  1. **Schema introspection round-trip:** call `describe_schema` against the ingested sample; every node label listed is actually present in the graph.
  2. **Run one of the example queries verbatim** (the dynamic-dispatch one); assert it returns the differentiator edge. **This is the Phase 6 acceptance test** — proves an agent following the documented examples can reproduce the runtime-only-calls signal through `run_cypher`.
  3. **Read transaction blocks a write the lexer missed:** call the inner runner directly with a CREATE-containing query; assert the engine refuses.
  4. **Timeout fires:** ridiculously expensive `MATCH (n)-[*1..15]->(m)` query; assert `timeout` error.
  5. **`truncated: true` flag:** permissive MATCH that returns > row_limit rows; assert truncation flag set and `row_count == row_limit`.
  6. **`$project` auto-injection works against the real graph:** query referencing `$project` without passing it explicitly returns the sample project's data.

## 10. Repo Layout After Phase 6

```
livegraph/
  mcp/
    cypher_guard.py                    NEW
    tools.py                           + describe_schema(), run_cypher()
    server.py                          + 2 new @mcp.tool() wrappers
  config.py                            + 2 new settings
tests/
  unit/
    test_cypher_guard.py               NEW (~12 tests)
    test_mcp_tools_query.py            NEW (~6 tests)
  integration/
    test_cypher_query_integration.py   NEW (~6 tests)
README.md                              + section on agent-side Cypher
```

## 11. Risks

| Risk | Mitigation |
|---|---|
| Agent generates a Cypher query that hits a complexity wall | Server-side timeout + clear error; agent retries with tighter filters |
| Lexer false-positive on a valid query with a forbidden keyword in a string literal | Documented in the error message; agent re-issues with the constant parameterized |
| Lexer false-negative (forbidden keyword in a string literal somehow makes a real write) | Read transaction is the actual enforcement; engine refuses any write |
| Agent ignores `$project` and queries across all ingested projects | `describe_schema.safety.convention` makes scoping explicit; if agent omits it they get cross-project results — same risk as a developer running raw Cypher in Browser |
| Blanket `CALL` ban locks out useful introspection (`db.labels()` etc.) | Acceptable for v1; `describe_schema` exposes the equivalent info statically; procedure allowlist deferred |
| Very large result sets exceed MCP message size | `row_limit` default 1000; agent sees `truncated: true` and refines |
| Agent generates Cypher that contains `LIMIT 999999` to bypass auto-LIMIT | `row_limit` is still enforced server-side by truncating the result set after fetch; the LIMIT in the query is an optimization hint, not a contract |

## 12. Future Work (out of scope)

- A procedure allowlist for read-only `CALL ...` (e.g., `db.labels()`, `db.schema.visualization()`).
- A `livegraph query "MATCH ..."` CLI subcommand for human ad-hoc use without an MCP host.
- An EXPLAIN-plan inspector for stricter validation than the lexer.
- A `query_template(name, params)` MCP tool that exposes the example queries by name (saves prompt tokens when the agent doesn't need to re-derive idioms).
- Result pagination / cursor API.
- Adding `runtime_stale` to every existing MCP tool's return shape (the property is exposed via `describe_schema` in Phase 6, so the gap from Phase 5 is partially closed).
