# livegraph Phase 10 — git-history layer (design)

**Date:** 2026-05-30
**Status:** Approved

## Goal

Add a time/authorship axis to the graph. Attach `(:Commit)` and
`(:Author)` nodes, attribute each commit's line-level changes to the
symbols whose current source spans those lines, and expose three new MCP
tools (`symbol_history`, `recent_changes`, `top_churn`) on top — taking
the total to 18.

## Non-goals

- Symbol-level history *as of each historical SHA* (requires re-parsing
  every commit; out of scope, may come behind `--accurate` later).
- Merging duplicate authors by name heuristics (we respect `.mailmap`
  and otherwise key on email).
- Tracking renames / moved files across history (we attribute by
  current path; renames before the ingest window land as file-level
  fallback only).
- Continuously refreshing history on file save (Phase 8 `watch` stays
  static + runtime only).

## Ingest

### Command

```
livegraph ingest-history [PATH]
    [--project NAME]
    [--since-last]   # only walk commits newer than the last ingested SHA
    [--max-commits N]  # cap, for big repos
```

- Foreground; logs progress; idempotent via `MERGE`.
- Default behavior re-ingests the full history. `--since-last` reads
  `Project.last_history_sha` and walks from there.

### Pipeline

1. **Discover commits.** Shell out to
   `git log --first-parent --no-merges --numstat --date=iso-strict
   --pretty=format:'%H%x1f%h%x1f%ai%x1f%aE%x1f%aN%x1f%s'`. The
   `%x1f` separator (ASCII unit separator) keeps subjects with `\t`
   intact. (`--first-parent` keeps history linear; merge bodies double
   count otherwise.)
2. **Respect `.mailmap`.** If the project root has a `.mailmap`, git
   already applies it during `git log` for `%aE` / `%aN`. No extra
   handling needed.
3. **Parse line ranges.** For each numstat row (`adds<TAB>dels<TAB>path`)
   we have totals but no line ranges. To attribute hunks to symbols, we
   ask `git log --no-merges --numstat -m --first-parent` per commit?
   No — instead run **one** `git diff-tree -p --no-renames --no-color
   <sha>^ <sha> -- <path>` per (commit, file) pair, parse the hunk
   headers (`@@ -a,b +c,d @@`), and collect the post-image line ranges.
   This is one `git diff-tree` per touched file per commit (proportional
   to total file-changes — bounded by the project's history size).
4. **Attribute hunks to symbols.** For each (file, hunk_line_range),
   query the graph for `(:File {path})-[:DEFINES|HAS_METHOD*1..2]
   ->(s:Symbol)` where `s.start_line ≤ hunk_end AND s.end_line ≥
   hunk_start`. If at least one symbol overlaps, write a
   `(s)-[:CHANGED_IN]->(c)` edge with
   `lines_overlapped = overlap_count`. If no symbol overlaps (whitespace
   change, import block, top-level statement), record only the
   file-level edge.
5. **Write nodes + edges.** Batched UNWIND, mirroring the Phase 1
   writer pattern. New writer file: `livegraph/history/writer.py`.
6. **Update `Project.last_history_sha`** to `HEAD` at end of run so
   `--since-last` works on the next invocation.

### Schema

```
(:Commit {
    sha,             // 40-hex, unique
    short_sha,
    message,         // subject line (first line of %s)
    timestamp,       // ISO-8601 string (sortable; Neo4j datetime() too)
    author_email,
})

(:Author {
    email,           // identity key
    name,            // last-seen name
})

(:Project {
    ...,
    last_history_sha,  // new property, optional
})

(:Author)-[:AUTHORED]->(:Commit)

// File-level fallback — always written for every (file, commit) pair.
(:File)-[:CHANGED_IN {
    additions, deletions,
}]->(:Commit)

// Symbol-level — written when at least one symbol overlaps the hunk.
// One edge per (symbol, commit) pair; lines_overlapped accumulates
// across all hunks in that commit that touched the symbol.
(:Symbol)-[:CHANGED_IN {
    lines_overlapped,
}]->(:Commit)

(:Project)-[:CONTAINS]->(:Commit)
```

`Symbol` is the existing secondary label that Phase 7 added to
`Function` / `Method`. We keep using it so a single graph query can
target "any code symbol with history."

### Phase 5 / Phase 8 interaction

`livegraph update` (Phase 5) re-parses changed files and rewrites
their `:Symbol` `start_line` / `end_line`. The `:CHANGED_IN` edges on
those symbols become stale (they attribute past hunks to current line
ranges that no longer overlap). Two choices:

- **Strict**: drop `:CHANGED_IN` on a symbol when its file is reingested.
  Loses history until the next `ingest-history` run.
- **Lenient (chosen)**: leave `:CHANGED_IN` edges alone. Lines moved
  around but the attribution "this symbol was touched in this commit"
  remains true. `lines_overlapped` becomes a historical artifact.

Phase 5 stays unchanged. We document the trade-off.

`livegraph watch` (Phase 8) likewise leaves history edges alone.

## MCP tools

Three new tools registered in `livegraph/mcp/server.py`. Implementation
lives in a new module `livegraph/mcp/tools_history.py` (keeping
`tools.py` from growing further, same as Phase 9's
`tools_neighborhood.py`).

### `symbol_history(qualified_name, limit=20)`

Recent commits that touched the symbol, newest first.

Returns:
```json
{
  "qualified_name": "myproj.calc.Calculator.add",
  "commits": [
    {
      "sha": "abc123...",
      "short_sha": "abc123",
      "message": "fix overflow in add",
      "timestamp": "2026-05-15T12:34:56Z",
      "author_email": "alice@corp.com",
      "author_name": "Alice Smith",
      "lines_overlapped": 7
    }
  ],
  "total_commits": 42,
  "warning": null
}
```

If no history is ingested, `commits=[]`, `total_commits=0`,
`warning="no git history ingested; run livegraph ingest-history"`.

### `recent_changes(since=null, limit=50, kind="any")`

Symbols changed in commits with `timestamp >= $since` (ISO-8601). If
`since` is null, returns the most recent `limit` commits' attributed
symbols. `kind` filters as elsewhere: `"any" | "function" | "method"`.

Returns:
```json
{
  "results": [
    {
      "qualified_name": "...",
      "kind": "method",
      "file": "...",
      "last_changed": "2026-05-29T08:00:00Z",
      "commit_count": 3,
      "latest_sha": "..."
    }
  ],
  "warning": null
}
```

Ordered by `last_changed DESC`.

### `top_churn(window_days=30, limit=20, kind="any")`

Top-K symbols by distinct commits in the window. Useful for spotting
hotspots.

Returns:
```json
{
  "window_days": 30,
  "results": [
    {
      "qualified_name": "...",
      "kind": "function",
      "file": "...",
      "commit_count": 14,
      "unique_authors": 3,
      "first_changed": "2026-05-01T00:00:00Z",
      "last_changed": "2026-05-29T00:00:00Z"
    }
  ],
  "warning": null
}
```

Ordered by `commit_count DESC, last_changed DESC` (stable tiebreak).

## File map

| File | Action | Responsibility |
|---|---|---|
| `livegraph/history/__init__.py` | Create | Package marker. |
| `livegraph/history/extractor.py` | Create | `iter_commits(root, since=None)` — shells out to `git log`, yields parsed `CommitRecord` + per-file `HunkRanges`. Pure I/O. |
| `livegraph/history/attributor.py` | Create | Given (file, hunk_ranges), look up overlapping symbols. Pure logic. Takes a backend; one query per file. |
| `livegraph/history/writer.py` | Create | Batched UNWIND writes for Commit/Author/CHANGED_IN/AUTHORED. |
| `livegraph/history/ingest.py` | Create | Orchestrator: iter → attribute → write. Returns an `IngestHistorySummary`. |
| `livegraph/cli.py` | Modify | New `ingest-history` subcommand. |
| `livegraph/mcp/tools_history.py` | Create | `symbol_history` / `recent_changes` / `top_churn` impls + their Cypher. |
| `livegraph/mcp/server.py` | Modify | Register the 3 new tools (16-18). Update "15 tools" → "18 tools". |
| `tests/integration/test_mcp_server_smoke.py` | Modify | Add the 3 names to the expected list. |
| `tests/unit/test_history_extractor.py` | Create | Uses `subprocess`-mocked or real-tempdir-git fixtures. |
| `tests/unit/test_history_attributor.py` | Create | Fake backend; overlap logic. |
| `tests/unit/test_history_writer.py` | Create | Canned-response backend; verifies UNWIND batches. |
| `tests/unit/test_history_ingest.py` | Create | Wiring; mocked extractor; idempotent re-run. |
| `tests/unit/test_mcp_tools_history.py` | Create | 3 tools × happy path + warning case. |
| `tests/unit/test_cli_ingest_history.py` | Create | Flag parsing, `--since-last`, missing project. |
| `tests/integration/test_history_integration.py` | Create | Real tempdir git repo + real Neo4j: ingest 3 commits, assert graph. |
| `README.md` | Modify | New "Git history" section. |

## Error handling

| Source | Behavior |
|---|---|
| `.git` missing | CLI exits with code 2 and a clear message: "not a git repository." |
| `git log` produces a row we can't parse | Log a warning with the SHA, skip the commit, continue. |
| `git diff-tree` fails for a (commit, file) | Log warning, write the file-level edge with `lines_overlapped` omitted from symbol attribution; continue. |
| Commit with 0 files changed (empty/merge) | Skip; don't write a Commit node. |
| Symbol overlap query returns nothing | Write only the file-level `:CHANGED_IN` edge. |
| `--since-last` but no `last_history_sha` | Behave as full ingest; log "first history ingest." |
| Neo4j unreachable | Exit 1 with the standard backend message. |

## Performance

For a 5k-commit history at ~3 files/commit, the pipeline is ~15k
`git diff-tree` invocations. That's a few minutes single-threaded; we
batch all writes per-commit so Neo4j is not the bottleneck. The
attributor query is one round-trip per (commit, file), so
~15k Neo4j reads — acceptable for a one-shot ingest but worth
caching `symbols_by_file` per file when we revisit. (Out of scope for
Phase 10; revisit if anyone files a perf complaint.)

`--since-last` makes incremental ingests near-free.

## Testing

### Unit
- `test_history_extractor.py` — given a tempdir with `git init` and a
  scripted commit sequence, `iter_commits` returns the expected
  records and hunk ranges; handles `.mailmap`; respects `since=SHA`.
- `test_history_attributor.py` — fake backend with canned File→Symbol
  rows; verifies which hunks attribute to which symbols (overlap, no
  overlap, partial overlap, multiple overlaps).
- `test_history_writer.py` — canned-response backend; verifies the
  UNWIND batches for Commit / Author / CHANGED_IN / AUTHORED, and
  that re-running with the same input is idempotent.
- `test_history_ingest.py` — mocks `iter_commits` and `_attribute`;
  verifies orchestration, `last_history_sha` update, and that
  `--since-last` calls `iter_commits` with the stored SHA.
- `test_mcp_tools_history.py` — fake backend; covers each of the 3
  tools' happy path, the "no history ingested" warning path, and
  parameter clamping.
- `test_cli_ingest_history.py` — Typer help, missing project (exit 2),
  not-a-git-repo (exit 2), `--since-last` flag plumbing.

### Integration
- `test_history_integration.py` — set up a tempdir git repo with 3-4
  scripted commits across two files, run `livegraph ingest` then
  `livegraph ingest-history`, then call `symbol_history` /
  `recent_changes` / `top_churn` and assert the graph matches the
  intent. Marks: `pytest.mark.integration`.
- `test_mcp_server_smoke.py` — already in the project; just extend the
  expected tool list.

## Out of scope (future phases)

- Symbol-level history that re-parses each historical SHA
  (`--accurate` flag).
- Cross-rename attribution.
- Author identity merging beyond `.mailmap`.
- `livegraph watch`-style live history (refresh on `post-commit` hook
  could come later as a thin adapter).
