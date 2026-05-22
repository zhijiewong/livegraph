# livegraph — Design Specification

- **Date:** 2026-05-23
- **Status:** Approved (design); pending implementation plan
- **Scope of this spec:** Phase 1 (static graph) + Phase 2 (runtime augmentation). The agent-facing layer (MCP server, NL queries, embeddings) is a future, separate spec.

---

## 1. Overview

`livegraph` is a local command-line tool that builds a knowledge graph of a **Python codebase** in a graph database (Neo4j by default, behind a swappable backend adapter), fusing two sources of truth:

- **Static analysis** — parse every `.py` file with tree-sitter and extract structure (files, classes, functions, methods, imports) and best-effort call edges.
- **Runtime observation** — run the target project's `pytest` suite under instrumentation, capture the calls that *actually executed* and per-test coverage, and merge them into the same graph.

Every relationship in the graph carries **provenance**: whether a fact came from the AST (`static`), from execution (`runtime`), or both. This makes the graph able to answer questions no purely static code-graph tool can — most importantly, *which calls did static analysis miss?* (dynamic dispatch, dependency injection, decorators, duck typing).

## 2. Background & Rationale

The original idea was a static Graph-RAG daemon: tree-sitter → graph database → MCP server. Research found that space is already well served by mature, open-source, MCP-native tools (`code-graph-rag`, `CodeGraphContext`, `GitNexus`, `Blarify`, `Potpie`, `Serena`, Aider's repo-map). Building another static graph tool would be an undifferentiated entry in a crowded field.

The clear white space: **every one of those tools is static-only.** They infer call edges by name-matching the AST, which research repeatedly shows is unreliable for dynamic dispatch, decorators, DI, and reflection. None of them incorporate runtime data — actual executed call paths, test coverage, test→symbol links — even though that data is produced by any test run.

`livegraph`'s differentiator is **static + runtime fusion**. Runtime traces do not merely add data; they *fix the accuracy problem*: a call static analysis cannot resolve is trivially resolved by observing one test run. This attacks the field's two most-cited weaknesses (static-only graphs, inaccurate call graphs) at once, and maps cleanly onto a local Python / tree-sitter / graph-database stack.

## 3. Scope

**In scope (this spec):**

- Phase 1 — static graph construction for Python source.
- Phase 2 — runtime augmentation from the target's `pytest` suite.
- CLI, configuration, Docker-based Neo4j infrastructure, testing.

**Out of scope (future Phase 3, separate spec):**

- MCP server / agent-facing query tools.
- Natural-language → Cypher querying.
- Embeddings / semantic search.
- Languages other than Python.
- Tracing anything other than `pytest` suites (e.g. arbitrary scripts, `unittest`).
- Incremental / file-watching updates.

## 4. Tech Stack & Rationale

**Language: Python 3.12+ (single language).**

Phase 2 is structurally locked to Python: `sys.monitoring` (PEP 669) only accepts Python callables as callbacks; a `pytest` plugin must be Python; `coverage.py` dynamic contexts must be Python. Sampling profilers (py-spy and other Rust tools) cannot substitute — they are statistical and would systematically miss call edges and produce a non-reproducible graph.

A Rust/C++ host would only help Phase 1 parsing and Bolt serialization — the two *smallest* costs — while the dominant cost (running the instrumented test suite) is immune to host language. tree-sitter parsing is native C in every binding. Every comparable Python code-graph tool is itself written in Python. A polyglot split costs a solo developer a second toolchain and a wheel-build matrix for ~zero payoff. If a hotspot ever appears under a profiler, the surgical fix is a small PyO3 extension for that one function — earned with evidence, not assumed.

**Stack:**

- `tree-sitter` + `tree-sitter-python` — parsing, with extraction driven by `.scm` query files so traversal stays in C.
- `neo4j` driver + `neo4j-rust-ext` — Bolt protocol to Neo4j. `neo4j-rust-ext` is Neo4j's own drop-in `pip install` accelerator, giving 1.5–4× faster writes (Rust-accelerated PackStream encoding) with no code or build complexity — it offsets Neo4j's relatively slower bulk-MERGE ingestion.
- `coverage.py` — per-test coverage via dynamic contexts (its `ctrace` core — the `sysmon` core does not support contexts).
- `sys.monitoring` (PEP 669) — call-edge capture, under a dedicated tool ID.
- `pytest` — the runtime entrypoint; a `livegraph` plugin provides per-test boundaries.
- `pydantic` / `pydantic-settings` — typed configuration from `.env`.
- `typer` — CLI.
- Neo4j via Docker Compose (the official `neo4j` image, which bundles Neo4j Browser for graph visualization).

**Graph-database choice.** Neo4j is the v1 backend: it is the most mature option, with the best Python driver and documentation — a real asset for a first build. It is server-based (a Docker container `livegraph` talks to over Bolt); its one weakness, slower bulk-MERGE ingestion, is irrelevant at v1 scale (thousands of nodes) and further offset by `neo4j-rust-ext`. All database access goes through a `GraphBackend` adapter (see Section 5) so the backend can be swapped later — e.g. to an embedded database once a stable one exists — without touching ingestion code.

Code is strictly typed (`mypy` strict), heavily commented, and modular.

## 5. Architecture & Module Layout

Two phases write to one graph. Phase 1 creates static nodes and edges; Phase 2 enriches the *same* nodes — setting `runtime` flags, adding `Test` labels, and writing coverage. `build` runs both.

All database access is mediated by a `GraphBackend` adapter interface in `graph/backend.py`. v1 ships a single Neo4j implementation; `ingest.py`, `augment.py`, and `writer.py` depend only on the interface, so the backend can later be swapped without touching ingestion logic.

```
livegraph/
  livegraph/
    cli.py              Typer CLI — ingest / trace / build / clean / status
    config.py           .env loading via Pydantic Settings
    models.py           Typed dataclasses for every extracted record
    ingest.py           Phase 1 orchestrator
    augment.py          Phase 2 orchestrator
    graph/
      backend.py        GraphBackend adapter interface + Neo4j implementation
      client.py         Connection + session management
      schema.py         Label/edge constants, constraint & index setup
      writer.py         Batched UNWIND + MERGE Cypher writes
    static/
      parser.py         tree-sitter setup; source -> AST
      extractor.py      AST walk (via .scm queries) -> records
      resolver.py       Import resolution + best-effort static call resolution
    runtime/
      runner.py         Invokes pytest in the target's environment
      pytest_plugin.py  Per-test boundaries
      tracer.py         sys.monitoring CALL/RETURN capture + qualified-name mapping
      coverage_adapter.py  coverage.py dynamic-contexts integration
  queries/python.scm    tree-sitter extraction queries
  tests/
    unit/
    integration/
    fixtures/sample_project/   tiny project + tiny pytest suite
  docker-compose.yml
  pyproject.toml
  .env.example
  .gitignore
  README.md
```

## 6. Node Identity — Qualified Names

Phases 1 and 2 must produce **identical node IDs**, or the merge fails. Every symbol has a stable `qualified_name`:

- File → project-relative path: `src/app/handlers.py`
- Function → `src/app/handlers.py::process_order`
- Method → `src/app/handlers.py::OrderHandler.process`
- Class → `src/app/handlers.py::OrderHandler`

At runtime, `sys.monitoring` provides `code.co_filename`, `code.co_qualname`, and `co_firstlineno`. The tracer maps `co_filename` → project-relative path and `co_qualname` (e.g. `OrderHandler.process`, `outer.<locals>.inner`) → the same `qualified_name` form.

This mapping is the highest-risk component of the build. v1 reliably maps top-level functions and methods. Frames it cannot map cleanly (some nested functions, lambdas) are **logged and tallied in a run summary — never silently dropped**. The mapping has dedicated unit tests against synthetic code objects.

## 7. Graph Schema

The organizing principle: **every relationship carries provenance.**

### Nodes

| Label | Unique key | Key properties | Created in |
|---|---|---|---|
| `Project` | `name` | `root_path` | Phase 1 |
| `File` | `path` (relative to root) | `name`, `language`, `parse_error` | Phase 1 |
| `Module` | `name` | `kind`: `stdlib` \| `thirdparty` | Phase 1 |
| `Class` | `qualified_name` | `name`, `file`, `start_line`, `end_line`, `decorators[]`, `source` | Phase 1 |
| `Function` | `qualified_name` | `name`, `file`, `start_line`, `end_line`, `decorators[]`, `source` | Phase 1 |
| `Method` | `qualified_name` | `name`, `class`, `file`, `start_line`, `end_line`, `decorators[]`, `source` | Phase 1 |

`source` holds the raw source text of the definition.

**`Test` is not a separate node — it is an added label.** A pytest test is a function already parsed in Phase 1. Phase 2 `MERGE`s on its `qualified_name` and attaches the `:Test` label plus `test_outcome` (`passed`/`failed`/`skipped`) and `test_duration`. The node ends up labeled `:Function:Test`. No duplicates, no identity drift.

A runtime symbol with no matching Phase 1 node (e.g. from a parse-failed file, or dynamically generated code) gets a minimal node flagged `runtime_only=true`.

### Edges

| Edge | From → To | Properties | Provenance |
|---|---|---|---|
| `CONTAINS` | `Project` → `File` | — | Phase 1 |
| `DEFINES` | `File` → `Class` \| `Function` | — | Phase 1 |
| `HAS_METHOD` | `Class` → `Method` | — | Phase 1 |
| `IMPORTS` | `File` → `File` \| `Module` | `raw`, `line` | Phase 1 |
| `CALLS` | `Function`\|`Method` → `Function`\|`Method` | `static` (bool), `runtime` (bool), `observed_count` (int), `call_site_lines` (int[]) | both phases |
| `COVERS` | `Test` → `Function`\|`Method` | `lines_covered`, `lines_total`, `coverage_pct` | Phase 2 |

**`CALLS` is the centerpiece.** Phase 1 creates it with `static=true, runtime=false`. Phase 2 `MERGE`s the same edge and sets `runtime=true`, `observed_count`, `call_site_lines`. The three resulting states are the core product value:

- `static=true, runtime=false` — AST predicted a call that never executed (dead code, or untested).
- `static=false, runtime=true` — **runtime caught a call static analysis missed** (the differentiator).
- `static=true, runtime=true` — confirmed.

**Aggregate coverage** is written as properties on each `Function`/`Method` node in Phase 2 (`runtime_observed`, `coverage_pct`, `lines_covered`, `lines_total`) for fast lookup without traversing `COVERS` edges.

**Constraints & indexes:** at startup Phase 1 creates a uniqueness constraint + index on the unique key of every node label, so `MERGE` is correct and fast. Idempotency comes entirely from `MERGE` on these keys — re-running either phase updates in place rather than duplicating.

## 8. Phase 1 — Static Ingestion (`ingest.py`)

1. **Discover** — walk the target directory for `.py` files; skip `.git`, `__pycache__`, `.venv`/`venv`/`env`, `.tox`, `build`, `dist`.
2. **Parse** (`static/parser.py`) — one reused tree-sitter `Parser`. Per file: read bytes, parse. If the tree has errors, log a warning, still create the `File` node with `parse_error=true`, skip its extraction, and **continue**. Unparseable files never abort the run.
3. **Extract** (`static/extractor.py`) — driven by `queries/python.scm` so traversal stays in C. Captures module-level functions, classes and their methods, import statements, and call-sites — each with name, qualified_name, line span, decorators, and raw `source` slice.
4. **Resolve static calls** (`static/resolver.py`) — build a project symbol table and per-file import bindings; resolve each call-site name against same-file scope then imports. Resolved to a project symbol → `CALLS` edge with `static=true`. Unresolved (stdlib/third-party/dynamic) → no edge. This is deliberately heuristic; runtime fills the gaps.
5. **Resolve imports** — dotted module → if it maps to a project file → `File-[:IMPORTS]->File`; else classify via `sys.stdlib_module_names` → `Module{kind}` and `File-[:IMPORTS]->Module`.
6. **Write** (`graph/writer.py`) — `schema.py` creates uniqueness constraints + indexes first; then batched `UNWIND $rows … MERGE`, one query per label/edge type, batch size from config. Fully idempotent.

## 9. Phase 2 — Runtime Augmentation (`augment.py`)

1. **Invoke** (`runtime/runner.py`) — spawn `<target_python> -m pytest` as a subprocess. The `livegraph` package is placed on `PYTHONPATH` so the target environment needs only `coverage` importable (a clear, fatal error is raised if it is missing). The plugin is injected with `-p livegraph.runtime.pytest_plugin`. The target interpreter defaults to the current environment or is supplied with `--python`.
2. **Plugin** (`runtime/pytest_plugin.py`):
   - `pytest_configure` — register the `livegraph` `sys.monitoring` tool under a dedicated tool ID for `CALL` / `PY_START` / `PY_RETURN` events; start `coverage.Coverage(dynamic_context="test_function")` (its `ctrace` core).
   - `pytest_runtest_call` — stamp the current test's `qualified_name` into shared state so the tracer can attribute observed calls.
   - `pytest_runtest_logreport` — record per-test outcome and duration.
   - `pytest_unconfigure` — stop monitoring and coverage; dump all observations to a JSON file.
3. **Tracer** (`runtime/tracer.py`) — `sys.monitoring` callbacks, filtered to project files by filename prefix to keep overhead low. Each event maps `co_filename` + `co_qualname` to a `qualified_name` and records `(caller, callee, current_test, call_site_line)` with counts.
4. **Coverage adapter** (`runtime/coverage_adapter.py`) — after the run, read per-context (per-test) coverage via the `coverage` API; map covered lines to Function/Method definitions using Phase 1's line spans.
5. **Merge** — the parent `livegraph` process reads the observations JSON and writes to Neo4j: add the `:Test` label + outcome/duration to test functions; `MERGE` each `CALLS` edge setting `runtime=true`, `observed_count`, `call_site_lines`; create `COVERS` edges with coverage properties; set aggregate coverage properties on Function/Method nodes. Runtime symbols with no Phase 1 node get a minimal `runtime_only=true` node.

**Design notes:**

- Phase 2 requires Phase 1 to have run (the nodes must exist to be enriched).
- The plugin runs in the target's interpreter and cannot share the Neo4j connection — it dumps observations to JSON; the parent process is the single graph writer.
- Running `coverage.py` (`ctrace`) and the `livegraph` `sys.monitoring` tool together is supported; tracing overhead is the known, accepted cost.

## 10. CLI, Configuration & Infrastructure

### CLI (`cli.py`, Typer)

| Command | Behavior |
|---|---|
| `livegraph ingest [PATH]` | Phase 1 only — static graph |
| `livegraph trace [PATH] [--python PY]` | Phase 2 only — requires Phase 1 already run |
| `livegraph build [PATH]` | Phase 1 + Phase 2 |
| `livegraph clean` | Wipe the graph (`MATCH (n) DETACH DELETE n`) |
| `livegraph status` | Node/edge counts, coverage summary, unmapped-frame tally |

### Configuration (`config.py`, Pydantic Settings, `.env`)

`NEO4J_URI` (`bolt://localhost:7687`), `NEO4J_USER` (`neo4j`), `NEO4J_PASSWORD` (required — Neo4j enforces authentication; `docker-compose.yml` and `.env.example` ship a matching default for local use), `LIVEGRAPH_BATCH_SIZE` (`1000`), `LIVEGRAPH_LOG_LEVEL` (`INFO`). `.env.example` is committed; `.env` is gitignored.

### Infrastructure

- **`docker-compose.yml`** — the official `neo4j` image, exposing Bolt on `7687` and **Neo4j Browser** on `7474` for visual inspection of the runtime-augmented graph, with named volumes for data persistence. Browser is bundled in the image — no separate container needed.
- **`pyproject.toml`** — package `livegraph`, Python 3.12+. Runtime deps: `tree-sitter`, `tree-sitter-python`, `neo4j`, `neo4j-rust-ext`, `pydantic`, `pydantic-settings`, `typer`, `coverage`. Dev deps: `pytest`, `mypy` (strict), `ruff`.

## 11. Error Handling

- **Unparseable file** — warn, create a `File` node with `parse_error=true`, continue.
- **Neo4j unreachable** — clear fatal message naming the connection target.
- **`coverage` missing in target env** — clear message; Phase 2 aborts cleanly; the Phase 1 graph remains valid.
- **Failing tests in the target suite** — do *not* abort the merge; everything observed is still recorded.
- **Unmappable runtime frames** — logged and counted in the run summary; never silently dropped.

## 12. Testing Strategy (TDD)

- **Unit** (no Docker required):
  - `extractor` against fixture `.py` files of known structure.
  - `resolver` — import resolution and static call resolution.
  - The high-risk **qualified-name mapping** (`co_qualname` → `qualified_name`), tested directly against synthetic code objects covering methods, nested functions, decorators, and lambdas.
- **Integration** (`@pytest.mark.integration`, requires a Docker Neo4j):
  - Run a full `build` against `tests/fixtures/sample_project/` and assert node/edge counts and provenance flags.
- **The differentiator test** — `tests/fixtures/sample_project/` contains a deliberate **dynamic-dispatch call** that static analysis cannot resolve. The test asserts that after Phase 2 the graph contains that `CALLS` edge with `static=false, runtime=true`. If this test passes, the project's core premise works.

Unit tests run without Docker; integration tests are opt-in.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Qualified-name join (`co_qualname` ↔ `qualified_name`) is imperfect for nested/lambda frames | v1 targets top-level functions and methods reliably; unmapped frames are logged and tallied, not dropped; dedicated unit tests. |
| Tracing overhead slows large test suites | `sys.monitoring` events filtered to project files only; overhead is accepted and documented. |
| Target environment lacks `coverage` | Detected up front with a clear, fatal message; Phase 1 graph stays valid. |
| Static call resolution produces false positives/negatives | Treated as best-effort; runtime provenance is the trusted signal; provenance flags make this visible rather than hidden. |
| `coverage.py` `ctrace` core and `livegraph` `sys.monitoring` tool interaction | Distinct `sys.monitoring` tool IDs; combination is supported; validated by the integration test. |

## 14. Future Work (Phase 3 — Separate Spec)

- MCP server exposing read-only query tools to coding agents.
- Natural-language → Cypher querying.
- Embeddings / semantic search over symbols.
- Additional languages beyond Python.
- Incremental / file-watching graph updates.
- Tracing entrypoints beyond `pytest`.
