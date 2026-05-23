# livegraph Phase 5 — Incremental Updates Design Specification

- **Date:** 2026-05-23
- **Status:** Approved (design); pending implementation plan
- **Scope of this spec:** A new `livegraph update [PATH] [--project NAME] [--dry-run]` CLI command that re-ingests only the files whose content has changed since the last build, using SHA-256 content hashes stored on `File` nodes. Phase 2 (runtime) data is preserved but flagged `runtime_stale=true` on the changed-file symbols.
- **Out of scope (future):** file-watcher daemon (`livegraph watch`), MCP tool for incremental refresh, auto-running `livegraph trace` after `update`, diff-aware staleness (only flag symbols whose bodies actually changed), updating the MCP tools' return shapes to expose `runtime_stale`.
- **Builds on:** Phase 1+2 (graph + idempotent writer), Phase 3 (MCP server, unaffected), Phase 4 (`change_impact`, unaffected). Phase 5 is purely an additive maintenance command.

---

## 1. Overview

`livegraph update` brings an already-ingested project's graph back in sync with the current filesystem state. It walks the project root, computes a SHA-256 hash for every `.py` file, compares against the `content_hash` stored on each `File` node during ingest, and re-ingests only the files whose hash differs. Files that have disappeared from disk are removed from the graph; files newly on disk are added.

Phase 2 (runtime) data on changed files is preserved — not deleted, not overwritten — and flagged `runtime_stale=true` so agents and humans can see "this coverage was captured before the last edit." A subsequent `livegraph trace` run clears the flag on every symbol that appears in the new observations.

No daemon, no MCP tool. v5 is one CLI subcommand that closes the staleness loop with the smallest possible scope.

## 2. Rationale

Every prior phase has been undermined by one thing: the graph goes out of sync the moment the developer saves a file. `change_impact`, `runtime_only_calls`, `find_callers` — every tool we built — returns wrong answers against a stale graph. The Phase 1+2 spec, Phase 3 spec, and Phase 4 spec all listed "incremental / file-watching graph updates" as deferred future work; Phase 5 closes that loop.

The research from Phase 1+2 noted that **correct incremental graph maintenance is an unsolved problem in the field** — file-watcher daemons in comparable tools (CodeGraphContext, code-graph-rag) frequently get edge-invalidation wrong on signature changes. Phase 5 sidesteps the daemon-correctness rabbit hole by being explicitly *on-demand*: user edits files, runs `livegraph update`, gets a fresh graph. No race conditions with concurrent MCP queries, no signal handling, no daemon lifecycle.

The per-file re-ingest is correct by exploiting two existing design properties:

1. **The writer is already idempotent** — Phase 1's `MERGE (... {qualified_name: ...})` preserves incoming edges. Re-MERGE'ing a symbol updates its metadata without destroying who calls it.
2. **CALLS edges are file-scoped on the caller side** — Outgoing CALLS belong to the caller's file; on file change, only the changed file's outgoing CALLS need rewriting.

These two properties make per-file incremental correct in the common case. The known limitation is described in §5.

## 3. Scope

**In scope:**

- A `livegraph/incremental.py` module with `detect_changes()` and `reingest_files()`.
- A `livegraph update [PATH] [--project NAME] [--dry-run]` CLI subcommand.
- Two new properties on graph nodes: `File.content_hash` (SHA-256 hex) and `Project.root_path` (absolute path), written by Phase 1 ingest from now on.
- One new property on `Function`/`Method` nodes: `runtime_stale: bool`.
- Three new methods on `GraphWriter`: `delete_symbols`, `delete_outgoing_calls_for_file`, `delete_imports_from_file`.
- Phase 2 (`augment_from_observations`) clears `runtime_stale=false` on every symbol that appears in the new observations.
- Unit, integration, and dry-run tests.
- A README section describing the workflow.

**Out of scope (future, separate specs):**

- `livegraph watch` daemon.
- An MCP tool that triggers `update` from the agent's side.
- Auto-running `livegraph trace` after `update`.
- Diff-aware staleness flagging (using Phase 4's diff parser to flag only when a symbol's body changed, not on comment-only edits).
- Updating the MCP tool return shapes to expose `runtime_stale` directly (mechanical follow-up — `runtime_stale` is stored on nodes and is queryable today, but tools don't surface it in their RETURN clauses yet).

## 4. Tech Stack

No new runtime dependencies. `hashlib.sha256` (stdlib), `os.walk` (stdlib), the existing `neo4j` driver, the existing `tree-sitter` parser, and the existing Phase-1 extractor and resolver. The `Settings` and CLI follow the established Phase 3 patterns.

## 5. Architecture

### Module layout

```
livegraph/
  incremental.py    NEW — detect_changes() + reingest_files() orchestrator
  ingest.py         + _ingest_single_file() helper used by both build and update
                    + Phase 1 stores content_hash on File and root_path on Project
  cli.py            + `update` subcommand
  models.py         + content_hash: str | None field on FileRecord
  graph/writer.py   + delete_symbols(qns)
                    + delete_outgoing_calls_for_file(file)
                    + delete_imports_from_file(file)
                    + flag_runtime_stale_for_file(file)
                    + clear_runtime_stale_for_symbols(qns)
  augment.py        + after writing observations, clear runtime_stale on
                      every symbol that appeared (caller, callee, or covered)
```

### Data flow (one `livegraph update` run)

```
livegraph update --project sample
   │
   ▼
1. Resolve project + root_path from the Project node
   │
   ▼
2. Walk filesystem under root_path, compute SHA-256 of each .py file
   │
   ▼
3. Query Neo4j for stored {File.path: File.content_hash} in the project
   │
   ▼
4. Classify each file: { unchanged | changed | added | deleted }
   │
   ▼
5. For each deleted file:
     DETACH DELETE its symbols and File node
   │
   ▼
6. For each changed/added file (any order):
     a. Parse with tree-sitter
     b. Extract definitions + raw_calls + imports
     c. old_qns - new_qns -> DETACH DELETE removed symbols
     d. MERGE new definitions (idempotent upsert preserves incoming CALLS)
     e. Delete this file's outgoing CALLS edges
     f. Re-resolve calls in this file -> write new outgoing CALLS
     g. Delete this file's IMPORTS edges; re-resolve and re-write them
     h. Flag this file's symbols runtime_stale=true
     i. UPDATE File.content_hash to the new hash
   │
   ▼
7. Print summary: N changed, M added, K deleted, P unchanged
```

### Known correctness limitation

The per-file re-ingest is correct in the common case but has one edge:

**Renames-across-files.** If file A defines `helper`, file B calls `helper`, and a `livegraph update` later sees A renamed `helper` to `helper2` (without B being touched), B's CALLS edge to `A.py::helper` is left orphaned (the symbol's `qualified_name` is no longer in the graph; B's stored CALLS edge points to a now-dangling target). The correct fix is to re-resolve B as well, which means re-parsing it.

v5 documents this and accepts it. The full `livegraph build` recovers from any stale state. Phase 6 (or a future polish) can add a "re-resolve callers of removed symbols" step.

## 6. Change Detection

### Stored state

Two graph properties Phase 1 must write from now on:

- `File.content_hash: str` — hex SHA-256 of the file's bytes at ingest time.
- `Project.root_path: str` — absolute filesystem path the project was built from. Stored once at first build; refreshed on subsequent builds. Used by `update` to walk the filesystem.

Both properties default to `None` for pre-Phase-5 graphs. A `File` with `content_hash IS NULL` compares not-equal to any new hash and is therefore classified as `changed` — re-ingested once, then hashed forever after. A `Project` with `root_path IS NULL` requires the user to pass `PATH` explicitly on the first `update` (or to re-run `livegraph build` to populate it).

### `detect_changes` signature and algorithm

```python
def detect_changes(root: str, backend: GraphBackend,
                   project: str) -> ChangeSet
```

```python
@dataclass(frozen=True, slots=True)
class ChangeSet:
    added: list[str]      # rel paths on disk but not in graph
    changed: list[str]    # rel paths whose disk hash != stored hash
    deleted: list[str]    # rel paths in graph but not on disk
    unchanged: list[str]  # rel paths matching by hash
    hashes: dict[str, str]  # path -> sha256 hex, every on-disk file
```

Algorithm:

1. `stored = backend.execute("MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) RETURN f.path AS path, f.content_hash AS hash", project=project)` → `{path: hash}` map.
2. `discover_python_files(root)` (reused from Phase 1) → walk on-disk files.
3. For each on-disk file, read bytes and compute `hashlib.sha256(bytes).hexdigest()`.
4. Set arithmetic: `added = disk - stored`; `deleted = stored - disk`; for the intersection, `changed = hashes_disagree`, `unchanged = hashes_agree`.

`detect_changes` is a pure read. `--dry-run` returns at this point and prints the classification.

## 7. Per-File Re-Ingest

### `_ingest_single_file` (extracted from `ingest_project` for reuse)

Phase 1 already does this work inside its main loop; v5 lifts the per-file body into a reusable helper. Both `ingest_project` (full build) and `reingest_files` (update) call it.

```python
def _ingest_single_file(
    rel_path: str, source: bytes, backend: GraphBackend,
    writer: GraphWriter, project: str, content_hash: str,
    project_modules: dict[str, str], project_defined: set[str],
) -> _FileIngestResult
```

It is the SAME logic Phase 1 already performs per file — parse, extract, resolve, write — but parameterized by the project-wide `project_modules` map and `project_defined` set, so the caller controls when to recompute those.

### `reingest_files` (the v5 orchestrator)

```python
def reingest_files(
    root: str, backend: GraphBackend, project: str,
    changeset: ChangeSet,
) -> UpdateSummary
```

`reingest_files` runs in **two phases** so that all definitions are settled before any call/import resolution. This makes the order of files within a run irrelevant.

**Phase A — reconcile structure (per file, any order):**

1. For each path in `changeset.deleted`:
   - `DETACH DELETE` every symbol owned by that File and the File itself.
2. For each path in `changeset.added ∪ changeset.changed`:
   - Parse with tree-sitter; extract `(defs, imports, raw_calls)` and stash `imports` and `raw_calls` in memory keyed by `rel_path` for Phase B.
   - Compute `old_qns_for_file` and `new_qns_for_file = {d.qualified_name for d in defs}`.
   - `DETACH DELETE` `old_qns_for_file - new_qns_for_file`.
   - Re-MERGE the FileRecord with the new `content_hash`.
   - MERGE the new definitions.
   - `delete_outgoing_calls_for_file(rel_path)` — wipe stale CALLS (will be rewritten in Phase B).
   - `delete_imports_from_file(rel_path)` — wipe stale IMPORTS (will be rewritten in Phase B).
   - `flag_runtime_stale_for_file(rel_path)` — set `runtime_stale=true` on the file's Function/Method symbols.

**Phase B — resolve calls and imports (once, after all files):**

3. Read the project's now-final defined-set and module-map from the graph in one shot:
   - `project_defined: set[str]` — every `qualified_name` of `Function`/`Method`/`Class` reachable from the Project.
   - `project_modules: dict[str, str]` — dotted-module to File-path for every File in the project (rebuilt from current state).
4. For each reingested file, in any order:
   - Resolve its stashed `raw_calls` against `project_defined` → write its outgoing `CALLS` edges.
   - Resolve its stashed `imports` against `project_modules` → write its outgoing `IMPORTS` edges.

This two-phase design closes the order-doesn't-matter property: a file that changed to call a symbol another reingested file just added will resolve correctly, because resolution runs after every file's definitions are in place. The one limitation it still doesn't cover is described in §5.

### The new writer Cypher

```python
# delete_symbols(qns: Iterable[str])
"UNWIND $qns AS qn "
"MATCH (s {qualified_name: qn}) "
"WHERE s:Function OR s:Method OR s:Class "
"DETACH DELETE s"

# delete_outgoing_calls_for_file(rel_path: str)
"MATCH (s {file: $file})-[c:CALLS]->() "
"WHERE s:Function OR s:Method "
"DELETE c"

# delete_imports_from_file(rel_path: str)
"MATCH (src:File {path: $file})-[r:IMPORTS]->() "
"DELETE r"

# flag_runtime_stale_for_file(rel_path: str)
"MATCH (:Project {name: $project})-[:CONTAINS]->(:File {path: $file}) "
"      -[:DEFINES|HAS_METHOD*1..2]->(s) "
"WHERE s:Function OR s:Method "
"SET s.runtime_stale = true"

# clear_runtime_stale_for_symbols(qns: Iterable[str])
# Called by augment_from_observations after writing runtime data.
"UNWIND $qns AS qn "
"MATCH (s {qualified_name: qn}) "
"WHERE s:Function OR s:Method "
"SET s.runtime_stale = false"
```

The `delete_file_completely` operation needed for deleted files:

```python
# delete_file(rel_path: str)
"MATCH (:Project {name: $project})-[:CONTAINS]->(f:File {path: $file}) "
"OPTIONAL MATCH (f)-[:DEFINES|HAS_METHOD*1..2]->(s) "
"WHERE s:Function OR s:Method OR s:Class "
"DETACH DELETE s, f"
```

Project-scoping is preserved on every query — same defensive pattern Phase 3's reviewer-fix established.

## 8. Phase 2 (Runtime) Staleness Flagging

### The flag

One new property: **`Function.runtime_stale: bool`** and **`Method.runtime_stale: bool`** (treated identically). Default is `false`/unset.

- Set to `true` on every Function/Method whose file is re-ingested by `update`.
- Cleared (set to `false`) on every Function/Method that appears in a fresh `livegraph trace`'s observations.

### Setting the flag (in `reingest_files`)

After step 2.h above (`flag_runtime_stale_for_file`), each re-ingested file's symbols are flagged in one batched Cypher call.

### Clearing the flag (in `augment_from_observations`)

After `write_runtime_calls` and `write_coverage`, a single final pass collects the union of all `qualified_name`s observed in this trace run — every caller, every callee, every COVERS target — and runs:

```python
writer.clear_runtime_stale_for_symbols(observed_qns)
```

Symbols not in `observed_qns` keep their existing flag. A symbol that was previously fresh and was just edited but isn't covered by any test in this trace run stays `runtime_stale=true` — honest signal: "this code was edited, no test in the recent run touched it."

### What v5 does NOT do

- The MCP tools (`get_source`, `find_callers`, `tests_for`, `untested_symbols`, `change_impact`) do **not** add `runtime_stale` to their return shape in v5. The property is stored on the graph and queryable directly via Neo4j Browser. Surfacing it in every tool's output is a small mechanical follow-up — one RETURN column per query, one field per row-mapper — that's been deferred to keep Phase 5 tightly scoped.
- v5 does not distinguish comment-only edits from semantic edits. Every file change flags every symbol in that file.

## 9. CLI, Configuration & Error Handling

### CLI

```
livegraph update [PATH] [--project NAME] [--dry-run]
```

- `PATH` defaults to the project's stored `Project.root_path`. If both `PATH` and `root_path` are absent, error code 2.
- `--project NAME` overrides the `LIVEGRAPH_PROJECT` env. One of the two is required (same convention as `livegraph mcp`).
- `--dry-run` walks the filesystem and prints the classification (added/changed/deleted/unchanged) without writing.

Output of a normal run:

```
Project: sample (root: /Users/yvon.zhu/some-project)
Detected: 2 changed, 1 added, 0 deleted, 47 unchanged
Re-ingesting 3 files...
  changed: src/app/handlers.py (4 symbols, +1 -2)
  changed: src/app/utils.py    (8 symbols, +0 -0)
  added:   src/app/new_thing.py (3 symbols)
Flagged 15 symbols as runtime_stale.
Update complete.
```

### Configuration

No new settings. `LIVEGRAPH_PROJECT` and the Neo4j connection vars are reused.

### Error handling

| Failure | Behavior |
|---|---|
| `--project` missing and `LIVEGRAPH_PROJECT` unset | exit 2, message naming the env var |
| Project not in graph | exit 1, suggest `livegraph build` first |
| `Project.root_path` missing AND no PATH arg | exit 2, suggest `livegraph update PATH` or re-running `livegraph build` |
| Neo4j unreachable | exit 1, clear message naming `NEO4J_URI` |
| File on disk but unreadable | log warning, skip, continue |
| Parse error on a changed file | `parse_error=true` File node is upserted; symbols left unchanged; warning logged (same as Phase 1) |
| One file's re-ingest fails mid-run | abort that file, continue with the rest; print failures at the end; non-zero exit if any failed |

## 10. Testing Strategy

**Unit (no Neo4j):**

- `tests/unit/test_change_detection.py` — `detect_changes` against `FakeBackend` with canned stored-hashes responses. Cases: all-unchanged, one-changed, one-added, one-deleted, hash-null-is-changed (pre-Phase-5 graph), empty-stored = everything is added, deleted = all stored have no on-disk counterpart.
- `tests/unit/test_incremental.py` — `reingest_files` orchestrator against a queued backend. Cases: removed symbols are DETACH DELETED, outgoing CALLS are wiped before re-resolution, content_hash is updated, runtime_stale=true is set on file's symbols, parse-error path doesn't crash.
- `tests/unit/test_writer.py` — append tests for the four new writer methods.

~10–12 new unit tests.

**Integration (real Neo4j, reuses `ingested_sample` fixture):**

Each integration test does `shutil.copytree(sample_project_path, tmp_path)` so the on-disk fixture stays pristine and Phase 1/2/3/4 integration tests don't break.

1. **No-op:** call `update` on an unchanged copy; assert *no* re-ingest, every file is `unchanged`, graph counts unchanged.
2. **Add a function to `runner.py`, then `update`:** new symbol exists; `Calculator.add` unchanged; `runner.py::run_operation` still exists with the same `qualified_name` and incoming CALLS edges (including Phase 2's runtime-only dynamic-dispatch edge).
3. **Remove a method from `Calculator`, then `update`:** the removed Method node is DETACH DELETED; any CALLS edges to it are gone.
4. **Delete `runner.py`, then `update`:** the File's symbols are removed; `Calculator.add`'s incoming dynamic-dispatch CALLS edge from `run_operation` is gone.
5. **`runtime_stale` lifecycle:** edit `calculator.py`, run `update`, assert `Calculator.add.runtime_stale = true`; then `livegraph trace`, assert `Calculator.add.runtime_stale = false`.
6. **`--dry-run`:** edit a file; run `update --dry-run`; assert the report shows it as `changed` AND the graph is unmodified (the file's stored `content_hash` did NOT change).

~6 integration tests.

## 11. Repo Layout After Phase 5

```
livegraph/
  incremental.py                     NEW
  ingest.py                          + _ingest_single_file, content_hash, root_path
  cli.py                             + update subcommand
  models.py                          + FileRecord.content_hash
  graph/writer.py                    + 5 new methods
  augment.py                         + clear_runtime_stale_for_symbols pass
tests/
  unit/
    test_change_detection.py         NEW (~5 tests)
    test_incremental.py              NEW (~5 tests)
    test_writer.py                   + ~4 new tests for new writer methods
  integration/
    test_incremental_integration.py  NEW (~6 tests)
README.md                            updated with `livegraph update` section
```

## 12. Risks

| Risk | Mitigation |
|---|---|
| Renames-across-files leave dangling CALLS edges | Documented v1 limitation (§5); full `livegraph build` recovers. A future "re-resolve callers of removed symbols" step can address it. |
| Pre-Phase-5 graphs have no `content_hash` / `root_path` | Both default to NULL; null != any hash so first `update` re-ingests all (conservative correct fallback); first `build`/`update` populates them. |
| `update` race against an in-flight MCP query | v5 is on-demand CLI only; user controls timing. Documented as a known constraint; daemon mode (which would have this race) is explicitly out of scope. |
| Large project with many small edits triggers many partial re-ingests | Per-file batching mirrors Phase 1's design; same `LIVEGRAPH_BATCH_SIZE` setting applies. |
| Hash collision on a real file edit | SHA-256 collision risk for this use case is negligible; trade-off acceptable. |
| Pre-Phase-5 graph runs `update` and gets a project missing `root_path` | Error path explicitly handled (§9 table); user passes `PATH` once, or re-runs `livegraph build`. |

## 13. Future Work (out of scope)

- `livegraph watch` — a file-watcher daemon that triggers `update` on file save events. Requires solving race conditions with concurrent MCP queries and daemon lifecycle. The unsolved part of this problem space; deserves its own spec.
- An MCP tool `refresh_graph(paths)` that an agent can invoke before queries.
- Diff-aware staleness — using Phase 4's `parse_diff` to flag `runtime_stale` only on symbols whose bodies actually changed, leaving comment-only edits' runtime data fresh.
- Exposing `runtime_stale` in every MCP tool's RETURN columns / row mappers.
- Auto-running `livegraph trace` after `update` (optional `--trace` flag).
- Cross-language incremental (Phase 5 is Python-only since static parsing is Python-only).
