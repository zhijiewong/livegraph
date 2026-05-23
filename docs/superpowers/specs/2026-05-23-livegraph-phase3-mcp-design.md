# livegraph Phase 3 — MCP Server Design Specification

- **Date:** 2026-05-23
- **Status:** Approved (design); pending implementation plan
- **Scope of this spec:** A focused, read-only MCP (Model Context Protocol) server that exposes a livegraph-built Neo4j graph to coding agents through 10 curated structured tools.
- **Out of scope (future):** NL→Cypher querying, embeddings / semantic search, languages other than Python, incremental updates. These remain in section 14 of the Phase 1+2 spec as Future Work.
- **Builds on:** `docs/superpowers/specs/2026-05-23-livegraph-design.md` (Phase 1 + Phase 2, merged on `main`).

---

## 1. Overview

`livegraph mcp` is a local command-line MCP server. A coding-agent host (Claude Code, Cursor, etc.) launches it as a subprocess; it talks JSON-RPC over stdio; it exposes 10 read-only tools that query the Neo4j graph livegraph built during Phase 1 (static ingestion) and Phase 2 (runtime augmentation).

The 10 tools are deliberately structured, not free-form Cypher or natural-language. Every tool answers a clear, useful question. Two of them — `runtime_only_calls` and `dead_static_calls` — are queries no other code-graph MCP server can run because no other tool fuses static and runtime data: they reveal the dynamic-dispatch calls static analysis missed and the predicted calls that never executed. They are the differentiator made addressable to agents.

Each server instance is bound to **one ingested project** chosen at startup (`--project NAME` or `LIVEGRAPH_PROJECT` env var). Agent hosts that want multiple projects register multiple server entries. This keeps tool signatures lean — no `project` parameter — and matches how MCP is normally deployed.

## 2. Rationale

The Phase 1 + 2 graph is in Neo4j; today the only way to use it is to write Cypher in Neo4j Browser. That's fine for human inspection but useless to a coding agent. An MCP server is what turns the graph from "a database" into "a tool agents reach for during code work."

The minimal scope (no LLM, no embeddings) is a deliberate YAGNI: research while designing Phase 1+2 found NL→Cypher is what hurts comparable tools' accuracy, and embeddings are a substantial separate concern. Both can come later as their own specs.

## 3. Scope

**In scope:**

- A `livegraph mcp` CLI subcommand that runs the server.
- A new `livegraph/mcp/` package with the server, tool functions, schemas, and Cypher queries.
- 10 read-only tools (specified in §6).
- A `livegraph_project` configuration field.
- Unit, integration, and MCP-runtime smoke tests.
- README updates documenting MCP host configuration.

**Out of scope (future, separate specs):**

- Natural-language → Cypher generation (would add an LLM dependency).
- Embeddings / semantic search over symbols (would add a model + vector store).
- A `run_cypher` raw-query escape hatch (agents struggle with this; surfaces injection risk).
- Read-write tools (file editing, graph deletion from MCP).
- Tracing entrypoints beyond pytest (Phase 1+2 limitation).
- Additional languages.
- Incremental / file-watching updates.

## 4. Tech Stack

- Python 3.12+ (matches Phase 1+2).
- **Official MCP Python SDK** — `mcp>=1.0` (Anthropic's `modelcontextprotocol/python-sdk`). It supplies the stdio transport, tool registration, schema marshalling, and structured-error response model. New runtime dep.
- Existing livegraph stack: `neo4j` driver, `pydantic` / `pydantic-settings`, `typer` CLI. No new graph or model dependencies.

The MCP SDK transitively brings in `anyio` (already required for async stdio); `pydantic` is already a dep.

## 5. Architecture

A thin shim layer (`server.py`) sits on top of pure-function tools (`tools.py`) that take a `GraphBackend` and typed inputs and return typed outputs. The shim translates between MCP's runtime and these functions. This is the same separation-of-concerns pattern Phase 1+2 already use, and it means every tool is unit-testable against `FakeBackend` with no MCP machinery in the test.

### Module layout (new code)

```
livegraph/
  mcp/
    __init__.py
    server.py       MCP server entry point; registers tools; runs stdio loop
    tools.py        Pure functions, one per tool
    schemas.py      Pydantic models for tool inputs and outputs
    cypher.py       Parameterized Cypher queries, one per tool
  cli.py            +mcp subcommand
  config.py         +livegraph_project field
```

### Data flow

1. Agent host launches `livegraph mcp --project <name>` (or uses `LIVEGRAPH_PROJECT` env).
2. Server reads settings, opens a `Neo4jBackend`, verifies connectivity, registers the 10 tools with the MCP SDK using their Pydantic input/output schemas.
3. Server enters the stdio JSON-RPC loop.
4. On each tool call: parse args (Pydantic) → call the matching function in `tools.py` → marshal the typed result back to MCP → emit JSON-RPC response.
5. On stdin close: close the Neo4j backend and exit cleanly.

### Project scoping

Every Cypher query in `cypher.py` starts from the configured project's node:

```
MATCH (:Project {name: $project})-[:CONTAINS]->(:File)-[:DEFINES|HAS_METHOD*0..2]->(s)
```

Results never leak across projects, even when two ingested projects share the same `qualified_name` (a known Phase 1 limitation). The server stores `project` once at startup and passes it as a parameter to every query.

## 6. Tool API Specifications

All 10 tools are read-only and return JSON-serializable dicts. Tools that look something up return empty lists when nothing matches — errors are reserved for real failures (Neo4j unreachable, bad arg types). Inputs are validated via Pydantic.

### Common shapes

```python
SymbolRef = {
    "qualified_name": "src/app/h.py::Handler.run",
    "name": "run",
    "kind": "function" | "method" | "class",
    "file": "src/app/h.py",
    "start_line": 12, "end_line": 24,
}

CallEdgeProvenance = {
    "static": bool, "runtime": bool,
    "observed_count": int, "call_site_lines": list[int],
}
```

### Tools 1–4 · Navigation

| Tool | Signature | Returns |
|---|---|---|
| `find_symbol` | `(query: str, exact: bool = False, limit: int = 50)` | `list[SymbolRef]` — case-insensitive substring match on `name` unless `exact=True` |
| `get_source` | `(qualified_name: str)` | `SymbolRef` plus `decorators: list[str]`, `source: str`, `runtime_observed: bool`, `coverage_pct: float`; returns `null` if not found |
| `find_callers` | `(qualified_name: str, provenance: "any"\|"static"\|"runtime" = "any", limit: int = 50)` | `list[{caller: SymbolRef, edge: CallEdgeProvenance}]` |
| `find_callees` | `(qualified_name: str, provenance: "any"\|"static"\|"runtime" = "any", limit: int = 50)` | `list[{callee: SymbolRef, edge: CallEdgeProvenance}]` |

### Tools 5–6 · The Differentiators

```python
runtime_only_calls(file: str | None = None, limit: int = 100)
  -> list[{caller: SymbolRef, callee: SymbolRef, observed_count: int}]
```
Cypher predicate: `WHERE c.runtime = true AND coalesce(c.static, false) = false`. Optional `file` filter scopes to that file's outgoing edges.

```python
dead_static_calls(file: str | None = None, limit: int = 100)
  -> list[{caller: SymbolRef, callee: SymbolRef}]
```
Cypher predicate: `WHERE c.static = true AND coalesce(c.runtime, false) = false`. These are the calls AST predicted but no test exercised — dead code, or untested.

### Tools 7–8 · Tests & Coverage

```python
tests_for(qualified_name: str)
  -> list[{test: SymbolRef (also includes test_outcome, test_duration),
           coverage_pct: float, lines_covered: int, lines_total: int}]
```

```python
untested_symbols(file: str | None = None,
                 kind: "function" | "method" | "any" = "any",
                 limit: int = 100)
  -> list[SymbolRef]
```
Returns Functions/Methods where `runtime_observed` is null or false.

### Tools 9–10 · Structure & Status

```python
imports(file: str, direction: "out" | "in" = "out")
  -> direction="out": list[{target: str,
                            kind: "file"|"stdlib"|"thirdparty",
                            raw: str, line: int}]
     direction="in":  list[{source_file: str, raw: str, line: int}]
```

```python
graph_status()
  -> {project: str, files: int, classes: int, functions: int,
      methods: int, tests: int,
      calls_total: int, calls_runtime_only: int,
      calls_static_only: int, calls_both: int}
```

`graph_status` is what an agent calls first to know what it has. `calls_runtime_only > 0` is the proof livegraph is doing its job.

### Cross-cutting behavior

- **Limits & truncation:** every list-returning tool has a `limit` parameter with a sensible default; if the limit is exceeded the response includes `truncated: true` so the agent can refine its query.
- **Project scoping:** every query joins through the `Project` node — no cross-project leakage.
- **Output size:** `source` text from `get_source` can be large. We do not summarize or paginate inside a definition; we trust agents to ask for what they want.

## 7. CLI, Configuration & MCP Host Wiring

### CLI

```
livegraph mcp [--project NAME]
```

Added to `livegraph/cli.py`. Runs the server over stdio until stdin closes. `--project` overrides `LIVEGRAPH_PROJECT`; if neither is set, the server exits with a clear message naming the env var.

### Configuration

`Settings` gets one new optional field:

```python
livegraph_project: str | None = None
```

Everything else (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `LIVEGRAPH_BATCH_SIZE`, `LIVEGRAPH_LOG_LEVEL`) is reused unchanged. `.env.example` gains a `LIVEGRAPH_PROJECT=` line (documented as optional).

### Wiring into an MCP host

Example `.mcp.json` (Claude Code; Cursor uses an equivalent format):

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

One host can register multiple livegraph entries; agents see them as `livegraph-myproject:find_symbol`, `livegraph-otherproject:find_symbol`, etc.

## 8. Error Handling

| Failure | Behavior |
|---|---|
| `--project` missing and `LIVEGRAPH_PROJECT` unset at startup | exit code 2 with a message naming the env var |
| Project name does not exist in the graph | server still starts; every tool returns empty results with a `warning: "project '<name>' not found"` field |
| Neo4j unreachable at startup | exit code 1, message naming `NEO4J_URI` |
| Neo4j drops mid-session | tool returns a structured MCP error response; server stays up |
| Tool call has malformed args | Pydantic validation error returned as a structured MCP error |
| Unexpected exception inside a tool | caught at the server boundary; returned as MCP error; logged to stderr; server stays up |

## 9. Testing Strategy

**Unit (no Neo4j, no MCP runtime):** every tool in `tools.py` is tested directly against `FakeBackend` — happy path and empty-result path. Approximately 20 unit tests. These verify result shape and parameter passing; Cypher correctness is *not* validated here (FakeBackend returns canned rows).

**Cypher integration (real Neo4j, reuses Phase 1/2 fixture):** every tool runs against `tests/fixtures/sample_project/` after a fresh `livegraph build`. Approximately 10 integration tests, marked `@pytest.mark.integration`. The differentiator gets explicit coverage:

```python
def test_runtime_only_calls_finds_dynamic_dispatch(neo4j_backend, ingested):
    from livegraph.mcp.tools import runtime_only_calls
    results = runtime_only_calls(neo4j_backend, project="sample")
    pairs = {(r["caller"]["qualified_name"], r["callee"]["qualified_name"])
             for r in results}
    assert ("runner.py::run_operation",
            "calculator.py::Calculator.add") in pairs
```

**MCP-runtime smoke test:** one end-to-end test launches the server in-process via the MCP SDK's testing harness, lists tools (asserts all 10 are registered with their declared schemas), and invokes `graph_status` over the wire.

**Explicitly not automated:**

- The MCP SDK itself (it is a dependency).
- Real-agent discoverability — that is manual: configure `.mcp.json` in Claude Code, ask "show me the dynamic-dispatch calls in this project", confirm the agent finds and uses `runtime_only_calls`. This is documented in the README as the acceptance test.

## 10. Repo Layout After Phase 3

```
livegraph/
  mcp/
    __init__.py
    server.py
    schemas.py
    tools.py
    cypher.py
  cli.py                 (+ mcp subcommand)
  config.py              (+ livegraph_project field)
tests/
  unit/test_mcp_tools.py            ~20 tests, FakeBackend
  integration/
    test_mcp_tools_integration.py   ~10 tests, real Neo4j
    test_mcp_server_startup.py      1 SDK smoke test
README.md                (+ MCP host configuration section)
```

## 11. Risks

| Risk | Mitigation |
|---|---|
| MCP SDK API churn (`mcp>=1.0` is young) | Pin a tight version range in `pyproject.toml`; the wiring lives only in `server.py`, so an SDK upgrade is localized. |
| Tool-result responses too large for an agent context | `limit` parameter + `truncated: true` flag on every list tool; `source` text is the only unbounded field and is opt-in via `get_source`. |
| Cross-project qualified_name collisions in Neo4j | Every query joins through the `Project` node so results are scoped; same-name nodes in other projects are filtered out at query time. |
| Agents fail to discover or compose the tools well | Curated, narrow tool surface (10 tools); descriptive names and schemas; manual acceptance test documented in README. |
| Phase 1 idempotency-bug regression (Phase 1 re-running wipes Phase 2 provenance) | Already fixed at the writer layer; not specific to Phase 3, but Phase 3 tools assume that fix holds. |

## 12. Future Work (out of scope for this spec)

- Natural-language → Cypher tool that uses an LLM to translate questions into Cypher and runs the result, with a constrained query grammar to control hallucinations.
- Embeddings on Function/Method nodes (e.g., UniXcoder) plus a vector store for semantic "find me code that does X" queries.
- A `change_impact(diff)` tool that, given a git diff, computes affected symbols and the tests to run.
- Incremental / file-watching graph updates so the graph stays in sync without re-running `build`.
- Multi-language support starting with TypeScript.
