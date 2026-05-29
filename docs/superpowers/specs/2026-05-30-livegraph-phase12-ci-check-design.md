# livegraph Phase 12 — `livegraph check` CI mode (design)

**Date:** 2026-05-30
**Status:** Approved

## Goal

Turn the existing analysis surface into something CI can enforce. A new
`livegraph check` CLI command reads `.livegraph.toml` and runs four
config-driven checks (cycles, layering, churn, hubs) against the
project's graph, with strict exit codes (`0`/`1`/`2`) and either
human-readable or JSON output.

## Non-goals

- New graph data, new MCP tools, or new node/edge types.
- Auto-fixing violations.
- Coverage-percentage checks (Phase 2 has COVERS edges but a coverage
  check belongs to its own future phase).
- CI-host integrations (GitHub Annotations, Buildkite, etc.).
- Running `livegraph update` or `livegraph ingest-history` as a side
  effect — stays read-only.

## CLI surface

```
livegraph check [PATH]
    [--config PATH]      # default: search up from CWD for .livegraph.toml
    [--format text|json] # default: text
    [--fail-fast]        # stop at first failing check
    [--strict]           # promote staleness warning to exit 2
```

## Config: `.livegraph.toml`

Loaded via stdlib `tomllib` (no new dep). Located at the project root by
default, or specified via `--config`. The full v1 schema:

```toml
[project]
name = "myproj"                       # required; equivalent to --project

[checks.cycles]
enabled = true
scope = "module"                      # "call" | "module"
provenance = "any"                    # only used when scope = "call"
min_size = 2
max_cycles = 0                        # check passes if cycles_count <= max_cycles

[checks.layering]
enabled = true
edge_kind = "any"                     # "any" | "imports" | "calls"
max_violations = 0
layers = [
    { name = "web",    patterns = ["web/**", "api/**"] },
    { name = "domain", patterns = ["domain/**"] },
    { name = "infra",  patterns = ["infra/**", "db/**"] },
]

[checks.churn]
enabled = true
window_days = 30
hot_files_threshold = 10              # fail if any single symbol's commit_count > this
ignore = ["tests/**"]                 # globs to subtract from results

[checks.hubs]
enabled = false                       # off by default — requires per-project tuning
kind = "any"                          # "any" | "function" | "method"
min_fanin = 25
max_hubs = 0
```

### Schema notes

- Top-level `[project]` block is required; `name` populates the
  backend's project-scoping parameter.
- Each `[checks.<name>]` block is independent. Omitting one means
  "not configured" → skipped (counted as `skipped` in the summary,
  not `failed`).
- `enabled = false` → check appears as `skipped` with reason
  `"disabled in config"`.
- `max_*` thresholds are **inclusive ceilings**. A check passes if
  the actual count is `<= max_*`.
- `ignore` (cycles, churn, hubs) is a glob list. Items whose file
  path matches any glob are filtered out before counting against
  the threshold.
- Unknown keys → `WARN` in the report (not a hard error). Unknown
  top-level sections → same.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All enabled checks passed. |
| 1 | At least one enabled check failed (threshold exceeded). |
| 2 | Environment or config error: missing/malformed config, Neo4j unreachable, project not in graph, **and with `--strict`** any staleness drift. |

A `skipped` check (disabled in config) does **not** contribute to
exit code 1. A `WARN` (e.g. unknown key) does not either.

## Output

### `--format text` (default)

```
Project: myproj
Graph staleness: 3 files drifted from disk (run `livegraph update`)

[PASS] cycles (module)           0 / max 0
[FAIL] layering                  4 / max 0
  domain/calc.py -> web/handlers.py (imports)
  domain/auth/login.py -> web/handlers.py (imports)
  ... 2 more
[PASS] churn (30d)               max 7 commits / threshold 10
[SKIP] hubs                      disabled in config

Summary: 2 passed, 1 failed, 1 skipped, warnings: 1 (staleness)
```

- One header line per check with status + actual/threshold.
- Up to 5 offending items inline; "... N more" if truncated.
- Final summary line.

### `--format json`

```json
{
  "project": "myproj",
  "graph_staleness": {
    "drifted_files": 3,
    "warning": "run `livegraph update` to refresh"
  },
  "results": [
    {
      "check": "cycles",
      "status": "passed",
      "actual": 0,
      "threshold": 0,
      "items": []
    },
    {
      "check": "layering",
      "status": "failed",
      "actual": 4,
      "threshold": 0,
      "items": [
        {"from_file": "domain/calc.py", "to_file": "web/handlers.py",
         "from_layer": "domain", "to_layer": "web", "edge_kind": "imports"},
        ...
      ]
    },
    {"check": "churn", "status": "passed", "actual": 7, "threshold": 10},
    {"check": "hubs", "status": "skipped",
     "reason": "disabled in config"}
  ],
  "summary": {"passed": 2, "failed": 1, "skipped": 1, "warnings": 1},
  "exit_code": 1
}
```

The `items` field is full when JSON (no 5-item cap — machine-readable
consumers want everything).

## Architecture

New package `livegraph/check/`. Each check is a small adapter that
calls the existing implementation function (from Phases 10/11) and
maps its result into a uniform `CheckResult`.

### File map

| File | Action | Responsibility |
|---|---|---|
| `livegraph/check/__init__.py` | Create | Package marker; re-export `CheckResult`, `run_checks`. |
| `livegraph/check/models.py` | Create | `CheckResult`, `CheckStatus`, `ReportSummary` dataclasses. |
| `livegraph/check/config.py` | Create | TOML load + schema validation; returns a typed `CheckConfig`. |
| `livegraph/check/staleness.py` | Create | One Cypher + a `discover_python_files` walk; returns `StalenessReport`. |
| `livegraph/check/checks.py` | Create | One function per check (`check_cycles`, `check_layering`, `check_churn`, `check_hubs`); each takes config block + backend, returns `CheckResult`. |
| `livegraph/check/runner.py` | Create | `run_checks(config, backend) -> Report` — orchestrates, handles `fail_fast`, produces final summary. |
| `livegraph/check/report.py` | Create | Text and JSON renderers. |
| `livegraph/cli.py` | Modify | Register `check` subcommand. |
| `tests/unit/test_check_config.py` | Create | TOML load + validation. |
| `tests/unit/test_check_staleness.py` | Create | Hash-mismatch detection with fake backend + tmp_path. |
| `tests/unit/test_check_cycles.py` | Create | Adapter; uses fake backend. |
| `tests/unit/test_check_layering.py` | Create | Adapter. |
| `tests/unit/test_check_churn.py` | Create | Adapter + `ignore` glob. |
| `tests/unit/test_check_hubs.py` | Create | Adapter + disabled state. |
| `tests/unit/test_check_runner.py` | Create | Orchestrator: exit codes, fail-fast, skipped counting. |
| `tests/unit/test_check_report.py` | Create | Text + JSON renderers. |
| `tests/unit/test_cli_check.py` | Create | Flag parsing, missing config, `--strict`. |
| `tests/integration/test_check_integration.py` | Create | Real Neo4j + tmpdir; one full-cycle pass + one fail. |
| `README.md` | Modify | Add "CI mode" section. |

No new runtime deps.

## Check adapters

Each adapter follows the same shape:

```python
def check_cycles(cfg: CyclesConfig, backend, project) -> CheckResult:
    if not cfg.enabled:
        return CheckResult.skipped("cycles", "disabled in config")
    result = find_cycles(
        backend, project, scope=cfg.scope, provenance=cfg.provenance,
        min_size=cfg.min_size, limit=cfg.max_cycles + 100,  # +pad
    )
    if result["warning"]:
        return CheckResult.error("cycles", result["warning"])
    actual = len(result["cycles"])
    items = result["cycles"]
    status = "passed" if actual <= cfg.max_cycles else "failed"
    return CheckResult(
        check="cycles", status=status,
        actual=actual, threshold=cfg.max_cycles,
        items=items,
    )
```

Same shape for layering, churn (uses `top_churn` then filters to the
single `hot_files_threshold` rule), and hubs.

### Churn check semantics

`churn` is a per-symbol check: we run `top_churn(window_days, limit=100,
kind="any")` then count how many results have `commit_count >
hot_files_threshold` after filtering out symbols whose `file` matches
any glob in `ignore`. The check FAILS if any such symbol exists
(threshold is implicit: 0 hot-and-not-ignored symbols allowed). The
returned `actual` is the count of hot-and-not-ignored symbols; the
items are those symbols.

This is the "hot spot detector" framing — we don't gate on average
churn, we gate on existence of unexpected hotspots.

## Staleness probe

```python
def probe_staleness(root, backend, project) -> StalenessReport:
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
        "RETURN f.path AS path, f.content_hash AS hash",
        project=project,
    )
    stored = {r["path"]: r.get("hash") for r in rows}
    drifted = 0
    for rel in discover_python_files(root):
        with open(os.path.join(root, rel), "rb") as h:
            disk_hash = hashlib.sha256(h.read()).hexdigest()
        if stored.get(rel) != disk_hash:
            drifted += 1
    return StalenessReport(drifted_files=drifted)
```

Same logic as Phase 5's `detect_changes` (reuse if cheap; otherwise
duplicate the loop to keep the dependency direction clean).

If `drifted > 0`:
- Text format: print `Graph staleness: N files drifted...` at top.
- JSON format: populate `graph_staleness.drifted_files`.
- `--strict` set: exit code becomes 2.

## Error handling

| Source | Behavior |
|---|---|
| `.livegraph.toml` missing | exit 2, message: "no .livegraph.toml found at $PATH; pass --config or create one". |
| TOML parse error | exit 2 with the parser's line/column. |
| Unknown top-level section | warning, continue. |
| Unknown key inside a check block | warning, continue. |
| Unknown check name in the schema | warning, continue (forward-compat). |
| Required field missing (e.g. `[project] name`) | exit 2 with field name. |
| Neo4j unreachable | exit 2 standard backend message. |
| `:Project {name}` not in graph | exit 2: "project '$name' not in graph; run `livegraph build` first". |
| Check raises unexpectedly | check status `error` with the exception message; counts as a failure (exit 1). Other checks still run unless `--fail-fast`. |

## Testing

### Unit
- `test_check_config.py`: full schema parse, missing `[project]`, defaults, unknown keys → warnings, `enabled=false` semantics.
- `test_check_staleness.py`: tmp_path with known files + canned Neo4j responses; verifies drift count.
- `test_check_cycles.py` / `_layering.py` / `_churn.py` / `_hubs.py`: each adapter against fake backend; verifies `passed`/`failed`/`skipped`/`error` transitions.
- `test_check_runner.py`: orchestrator runs all enabled checks; `fail_fast` halts on first fail; computes correct exit code; staleness + `--strict` → exit 2.
- `test_check_report.py`: text format truncates to 5 items + "...N more"; JSON format includes full items.
- `test_cli_check.py`: Typer help, `--config PATH`, `--format` validation, `--strict`, missing config → exit 2.

### Integration
- `test_check_integration.py`: build a tiny synthetic graph (3 files, 1 import cycle, 1 layering violation), write a `.livegraph.toml`, run `run_checks`; assert one check FAILS (layering), one PASSES (cycles when `max_cycles=10`), JSON output matches. Marks: `pytest.mark.integration`.

## Performance

Each check is one Cypher query (the underlying Phase 10/11
implementation function does its own work). Staleness probe is one
query + a `discover_python_files` walk + per-file hash. For a
1000-file project the probe runs in ~50–200ms; the four checks
together in ~1s. CI-acceptable.

## Out of scope

- Coverage-percentage check (Phase 2 COVERS edges; future phase).
- Hub-of-hubs / second-order architectural metrics.
- Auto-fix / suggestion output.
- Multi-project checks in one run.
- GitHub Annotations / SARIF output formats.
