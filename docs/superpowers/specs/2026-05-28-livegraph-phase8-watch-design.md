# livegraph Phase 8 — `livegraph watch` (design)

**Date:** 2026-05-28
**Status:** Approved

## Goal

Turn livegraph from a batch tool into a live one: a foreground `livegraph watch` command that mirrors source-file edits into the Neo4j graph within ~500ms of save, so an MCP-connected agent always sees the current code.

## Non-goals

- Background daemon / pidfile / `start`/`stop`/`status` subcommands (YAGNI).
- Multi-project watching from a single process.
- Editor / LSP integration.
- Auto-running pytest to refresh runtime CALLS edges.
- Polling-only mode (watchdog already falls back to polling where native events are unavailable).

## CLI

```
livegraph watch [PATH] \
  --project NAME \
  [--embed] \
  [--debounce-ms 300] \
  [--ignore PATTERN]...
```

- Foreground process; logs each update; Ctrl-C stops cleanly.
- `--embed` opt-in: requires the `[semantic]` extra (Phase 7). When set, the embedding provider is loaded once at startup; if `[semantic]` is missing, fail fast with exit 1 and a clear message.
- `--debounce-ms` defaults to 300.
- `--ignore` adds glob patterns on top of the builtin ignores.

## Architecture

New package `livegraph/watch/` with three units:

### `watch/watcher.py`
- Owns a `watchdog.Observer`.
- Translates raw fs events into a normalized `ChangeEvent(path: Path, kind: Literal["modified", "created", "deleted"])` stream.
- Applies file filtering in the event handler (never enqueues noise):
  - `*.py` only.
  - Respects `.gitignore` if present at the project root.
  - Builtin ignores: `.git/`, `__pycache__/`, `.venv/`, `venv/`, `node_modules/`, and the configured Neo4j data dir.
  - User `--ignore` patterns layered on top.
- Pure I/O adapter. No graph logic.

### `watch/debouncer.py`
- Pulls `ChangeEvent`s off a `queue.Queue` and coalesces a burst into a `ChangeBatch(modified: set[Path], deleted: set[Path])` after the window elapses with no new events.
- Coalescing rules:
  - `modified` + `modified` → `modified`.
  - `created` + `modified` → `modified` (treat as modified for graph purposes).
  - `created` + `deleted` → cancel out.
  - `modified` + `deleted` → `deleted`.
- Exposes `next_batch(timeout) -> ChangeBatch | None`. Pure logic; unit-tested with a fake clock.

### `watch/loop.py`
- The orchestrator. Pseudocode:
  ```python
  def run(backend, project, watcher, debouncer, *, embed_provider=None):
      backoff = ExponentialBackoff(start=1.0, cap=30.0)
      while not stop_requested():
          batch = debouncer.next_batch(timeout=1.0)
          if batch is None:
              continue
          try:
              update_files(backend, project, batch)
              backoff.reset()
          except ParseError as e:
              log.info("parse error in %s: %s", e.path, e.reason)
              continue
          except BackendError as e:
              log.error("backend error: %s; backing off %.1fs", e, backoff.peek())
              time.sleep(backoff.next())
              continue
          if embed_provider is not None:
              try:
                  embed_project(backend, project, embed_provider)
              except Exception as e:
                  log.warning("embed step failed (continuing): %s", e)
  ```
- Owns: error classification, backoff, provider lifecycle (loaded once, not per batch).

## Data flow

```
fs events ──▶ Watcher ──ChangeEvent──▶ Debouncer ──ChangeBatch──▶ Loop
                                                                    │
                                                                    ├─▶ update_files(backend, project, batch)   [Phase 5]
                                                                    │
                                                                    └─▶ embed_project(... )  if --embed          [Phase 7]
```

- Watcher → Debouncer handoff: `queue.Queue[ChangeEvent]` (watchdog uses its own thread).
- The Loop runs on the main thread.

## Phase-5 surface change: `update_files`

The existing `livegraph update` walks the whole project comparing `content_hash`. For watch we want to drive it from an explicit file list. We extract the per-file logic into:

```python
def update_files(backend, project, paths: Iterable[Path]) -> UpdateSummary
```

…and have the existing `update` call into it after its scan. Both surfaces (batch CLI and watch loop) go through the same code path. No behavioral change to `livegraph update`.

## Runtime CALLS edges

Watch only re-runs static analysis. Runtime CALLS edges (Phase 2, populated by pytest) are left alone, matching `livegraph update`'s current behavior. Stale runtime edges to deleted/renamed symbols are a cosmetic issue cleaned up by a full `livegraph ingest`.

## Error handling

| Source | Level | Loop behavior |
|---|---|---|
| Parse errors (syntax in half-saved file) | `INFO` | Skip the file; continue. No backoff. |
| Backend errors (Neo4j unreachable, driver transient) | `ERROR` | Exponential backoff 1s → 2s → 4s → … cap 30s. Events keep enqueuing. Backoff resets on success. |
| Embedding errors (only with `--embed`) | `WARNING` | Update is still committed. No backoff. |
| Ctrl-C | — | Stop observer, drain in-flight batch, exit 0. |

## Configuration additions

In `livegraph/config.py`:
- `livegraph_watch_debounce_ms: int = 300`
- (No new env var for `--embed`; it's a CLI-only flag.)

## Testing

### Unit
- `test_debouncer.py` — fake clock; coalescing rules; window timing.
- `test_watcher_filtering.py` — synthetic events; `.py`-only, gitignore, builtin ignores, user `--ignore`.
- `test_loop.py` — fake backend + fake debouncer; `update_files` called per batch, `embed_project` only when provider is set, backoff on backend error, parse-error skip without backoff.
- `test_update_files.py` — created/modified/deleted handling, runtime CALLS preserved, untouched files untouched.
- `test_cli_watch.py` — flag parsing, help text, `--embed` without `[semantic]` exits 1.

### Integration (`pytest.mark.integration`)
- `test_watch_integration.py` — spawn watcher in a thread against a temp project dir, write/modify/delete files, assert graph reflects each change within ~1s. One test with `--embed` confirms `semantic_search` finds a newly-written symbol.

### Manual acceptance (documented in PR body)
- Edit a file in the sample project → "updated 1 file" log line within ~500ms → `find_symbol` over MCP reflects the change.

## Out of scope (future phases)

- Daemon mode with pidfile, log file, `start`/`stop`/`status`.
- Multi-project config.
- Auto-rerunning pytest to refresh runtime CALLS edges.
- LSP/editor integration.
