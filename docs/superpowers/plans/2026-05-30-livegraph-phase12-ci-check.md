# livegraph Phase 12 — `livegraph check` CI Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `livegraph check` CLI command driven by `.livegraph.toml` that runs four config-driven checks (cycles, layering, churn, hubs) against the project's graph, with exit codes `0`/`1`/`2` and text/JSON output for CI integration.

**Architecture:** New `livegraph/check/` package with one file per responsibility — config load, staleness probe, per-check adapters, runner, report renderer. Each check adapter wraps an existing Phase 10/11 implementation function (`find_cycles`, `layering_violations`, `top_churn`, `hubs`) and maps its result into a uniform `CheckResult`.

**Tech Stack:** Python 3.12+, stdlib `tomllib` (no new dep), existing Neo4j backend, existing Typer CLI.

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `livegraph/check/__init__.py` | Create | Package marker; re-export `CheckResult`, `run_checks`, `Report`. |
| `livegraph/check/models.py` | Create | `CheckResult`, `CheckStatus` literal, `StalenessReport`, `Report` dataclasses. Pure data. |
| `livegraph/check/config.py` | Create | TOML load + schema validation; returns typed `CheckConfig`. |
| `livegraph/check/staleness.py` | Create | One Cypher + `discover_python_files` walk; returns `StalenessReport`. |
| `livegraph/check/checks.py` | Create | One function per check (`check_cycles`, `check_layering`, `check_churn`, `check_hubs`). |
| `livegraph/check/runner.py` | Create | `run_checks(config, backend, staleness, fail_fast)` orchestrator + exit-code logic. |
| `livegraph/check/report.py` | Create | Text and JSON renderers. |
| `livegraph/cli.py` | Modify | Register the `check` subcommand. |
| `tests/unit/test_check_config.py` | Create | TOML parse + validation. |
| `tests/unit/test_check_staleness.py` | Create | Hash-mismatch detection with fake backend + tmp_path. |
| `tests/unit/test_check_checks.py` | Create | All four adapters' status transitions. |
| `tests/unit/test_check_runner.py` | Create | Exit codes, fail-fast, skipped counting, `strict` for staleness. |
| `tests/unit/test_check_report.py` | Create | Text + JSON renderers. |
| `tests/unit/test_cli_check.py` | Create | Flag parsing, missing config, `--strict`. |
| `tests/integration/test_check_integration.py` | Create | Real Neo4j + tmpdir; full pass + fail cycle. |
| `README.md` | Modify | "CI mode" section. |

No new runtime deps. No new MCP tools. CLI tool count: existing commands + `check`.

---

## Task 1: Data models

**Files:**
- Create: `livegraph/check/__init__.py`
- Create: `livegraph/check/models.py`

- [ ] **Step 1: Create the package init**

`livegraph/check/__init__.py`:

```python
from livegraph.check.models import (
    CheckResult, CheckStatus, Report, StalenessReport,
)

__all__ = ["CheckResult", "CheckStatus", "Report", "StalenessReport"]
```

- [ ] **Step 2: Create `models.py`**

```python
"""Data classes for the `livegraph check` CI mode."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CheckStatus = Literal["passed", "failed", "skipped", "error"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one check.

    - `passed` / `failed`: threshold compared with `actual`.
    - `skipped`: check disabled in config (reason in `reason`).
    - `error`: check could not run (e.g. underlying tool returned a
      warning). The reason is in `reason`. Counts as a failure for
      exit-code purposes.
    """

    check: str
    status: CheckStatus
    actual: int = 0
    threshold: int = 0
    items: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    reason: str | None = None

    @classmethod
    def skipped(cls, check: str, reason: str) -> "CheckResult":
        return cls(check=check, status="skipped", reason=reason)

    @classmethod
    def error(cls, check: str, reason: str) -> "CheckResult":
        return cls(check=check, status="error", reason=reason)


@dataclass(frozen=True, slots=True)
class StalenessReport:
    """Result of comparing on-disk file hashes to stored content_hash."""

    drifted_files: int

    @property
    def has_drift(self) -> bool:
        return self.drifted_files > 0


@dataclass(frozen=True, slots=True)
class Report:
    """The full check run's output."""

    project: str
    staleness: StalenessReport
    results: tuple[CheckResult, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results
                   if r.status in ("failed", "error"))

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")
```

- [ ] **Step 3: Smoke-import**

```
.venv/bin/python -c "from livegraph.check import CheckResult, Report, StalenessReport; print('ok')"
```
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add livegraph/check/__init__.py livegraph/check/models.py
git commit -m "feat(phase12): check package skeleton + data models"
```

---

## Task 2: TOML config loader

**Files:**
- Create: `livegraph/check/config.py`
- Test: `tests/unit/test_check_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_check_config.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from livegraph.check.config import (
    CheckConfig, ConfigError, load_config,
)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / ".livegraph.toml"
    p.write_text(text)
    return p


def test_minimal_valid_config(tmp_path):
    cfg_path = _write(tmp_path, '[project]\nname = "myproj"\n')
    cfg = load_config(cfg_path)
    assert isinstance(cfg, CheckConfig)
    assert cfg.project == "myproj"
    # Every check defaults to disabled when its section is missing.
    assert cfg.cycles.enabled is False
    assert cfg.layering.enabled is False
    assert cfg.churn.enabled is False
    assert cfg.hubs.enabled is False


def test_missing_project_raises_config_error(tmp_path):
    cfg_path = _write(tmp_path, "[checks.cycles]\nenabled = true\n")
    with pytest.raises(ConfigError, match="project.name"):
        load_config(cfg_path)


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_malformed_toml_raises_config_error(tmp_path):
    cfg_path = _write(tmp_path, "this isn't = valid = toml\n")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_cycles_check_full_parse(tmp_path):
    cfg_path = _write(tmp_path, """
[project]
name = "p"

[checks.cycles]
enabled = true
scope = "module"
provenance = "any"
min_size = 2
max_cycles = 0
""")
    cfg = load_config(cfg_path)
    assert cfg.cycles.enabled is True
    assert cfg.cycles.scope == "module"
    assert cfg.cycles.provenance == "any"
    assert cfg.cycles.min_size == 2
    assert cfg.cycles.max_cycles == 0


def test_layering_layers_parsed(tmp_path):
    cfg_path = _write(tmp_path, """
[project]
name = "p"

[checks.layering]
enabled = true
edge_kind = "imports"
max_violations = 0
layers = [
    { name = "web", patterns = ["web/**"] },
    { name = "domain", patterns = ["domain/**"] },
]
""")
    cfg = load_config(cfg_path)
    assert cfg.layering.enabled is True
    assert cfg.layering.edge_kind == "imports"
    assert [l["name"] for l in cfg.layering.layers] == ["web", "domain"]


def test_churn_ignore_parsed(tmp_path):
    cfg_path = _write(tmp_path, """
[project]
name = "p"

[checks.churn]
enabled = true
window_days = 30
hot_files_threshold = 10
ignore = ["tests/**"]
""")
    cfg = load_config(cfg_path)
    assert cfg.churn.enabled is True
    assert cfg.churn.window_days == 30
    assert cfg.churn.hot_files_threshold == 10
    assert cfg.churn.ignore == ("tests/**",)


def test_unknown_top_level_section_warns_but_loads(tmp_path):
    cfg_path = _write(tmp_path, """
[project]
name = "p"

[unknown_thing]
foo = "bar"
""")
    cfg = load_config(cfg_path)
    assert cfg.project == "p"
    assert any("unknown_thing" in w for w in cfg.warnings)


def test_unknown_key_in_check_block_warns(tmp_path):
    cfg_path = _write(tmp_path, """
[project]
name = "p"

[checks.cycles]
enabled = true
mystery_key = 42
""")
    cfg = load_config(cfg_path)
    assert any("mystery_key" in w for w in cfg.warnings)
```

- [ ] **Step 2: Run, expect collection error**

```
.venv/bin/python -m pytest tests/unit/test_check_config.py -v
```

- [ ] **Step 3: Implement `config.py`**

Create `livegraph/check/config.py`:

```python
"""TOML config loading for `livegraph check`."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or invalid."""


@dataclass(frozen=True, slots=True)
class CyclesConfig:
    enabled: bool = False
    scope: str = "module"
    provenance: str = "any"
    min_size: int = 2
    max_cycles: int = 0


@dataclass(frozen=True, slots=True)
class LayeringConfig:
    enabled: bool = False
    edge_kind: str = "any"
    max_violations: int = 0
    layers: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ChurnConfig:
    enabled: bool = False
    window_days: int = 30
    hot_files_threshold: int = 10
    ignore: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class HubsConfig:
    enabled: bool = False
    kind: str = "any"
    min_fanin: int = 25
    max_hubs: int = 0


@dataclass(frozen=True, slots=True)
class CheckConfig:
    project: str
    cycles: CyclesConfig = field(default_factory=CyclesConfig)
    layering: LayeringConfig = field(default_factory=LayeringConfig)
    churn: ChurnConfig = field(default_factory=ChurnConfig)
    hubs: HubsConfig = field(default_factory=HubsConfig)
    warnings: tuple[str, ...] = field(default_factory=tuple)


_KNOWN_TOP_LEVEL = {"project", "checks"}
_KNOWN_CHECKS = {"cycles", "layering", "churn", "hubs"}

_CYCLES_KEYS = {"enabled", "scope", "provenance", "min_size", "max_cycles"}
_LAYERING_KEYS = {"enabled", "edge_kind", "max_violations", "layers"}
_CHURN_KEYS = {"enabled", "window_days", "hot_files_threshold", "ignore"}
_HUBS_KEYS = {"enabled", "kind", "min_fanin", "max_hubs"}


def load_config(path: Path) -> CheckConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"malformed TOML in {path}: {exc}") from exc

    warnings: list[str] = []

    for top in data.keys():
        if top not in _KNOWN_TOP_LEVEL:
            warnings.append(f"unknown top-level section: [{top}]")

    project_block = data.get("project") or {}
    project_name = project_block.get("name")
    if not project_name:
        raise ConfigError("required field missing: project.name")

    checks = data.get("checks") or {}
    for ck in checks.keys():
        if ck not in _KNOWN_CHECKS:
            warnings.append(f"unknown check: [checks.{ck}]")

    def _warn_unknown(block: dict[str, Any], known: set[str],
                      section: str) -> None:
        for key in block.keys():
            if key not in known:
                warnings.append(
                    f"unknown key in [{section}]: {key}",
                )

    cycles_block = checks.get("cycles") or {}
    _warn_unknown(cycles_block, _CYCLES_KEYS, "checks.cycles")
    cycles = CyclesConfig(
        enabled=bool(cycles_block.get("enabled", False)),
        scope=str(cycles_block.get("scope", "module")),
        provenance=str(cycles_block.get("provenance", "any")),
        min_size=int(cycles_block.get("min_size", 2)),
        max_cycles=int(cycles_block.get("max_cycles", 0)),
    )

    layering_block = checks.get("layering") or {}
    _warn_unknown(layering_block, _LAYERING_KEYS, "checks.layering")
    layering = LayeringConfig(
        enabled=bool(layering_block.get("enabled", False)),
        edge_kind=str(layering_block.get("edge_kind", "any")),
        max_violations=int(layering_block.get("max_violations", 0)),
        layers=tuple(layering_block.get("layers") or ()),
    )

    churn_block = checks.get("churn") or {}
    _warn_unknown(churn_block, _CHURN_KEYS, "checks.churn")
    churn = ChurnConfig(
        enabled=bool(churn_block.get("enabled", False)),
        window_days=int(churn_block.get("window_days", 30)),
        hot_files_threshold=int(churn_block.get("hot_files_threshold", 10)),
        ignore=tuple(churn_block.get("ignore") or ()),
    )

    hubs_block = checks.get("hubs") or {}
    _warn_unknown(hubs_block, _HUBS_KEYS, "checks.hubs")
    hubs = HubsConfig(
        enabled=bool(hubs_block.get("enabled", False)),
        kind=str(hubs_block.get("kind", "any")),
        min_fanin=int(hubs_block.get("min_fanin", 25)),
        max_hubs=int(hubs_block.get("max_hubs", 0)),
    )

    return CheckConfig(
        project=project_name, cycles=cycles, layering=layering,
        churn=churn, hubs=hubs, warnings=tuple(warnings),
    )
```

- [ ] **Step 4: Run tests, expect all PASS**

```
.venv/bin/python -m pytest tests/unit/test_check_config.py -v
```

- [ ] **Step 5: Commit**

```bash
git add livegraph/check/config.py tests/unit/test_check_config.py
git commit -m "feat(phase12): TOML config loader with schema validation"
```

---

## Task 3: Staleness probe

**Files:**
- Create: `livegraph/check/staleness.py`
- Test: `tests/unit/test_check_staleness.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import hashlib
from typing import Any

from livegraph.check.staleness import probe_staleness


class _FakeBackend:
    def __init__(self, stored: dict[str, str]):
        self._stored = stored
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        return [{"path": p, "hash": h} for p, h in self._stored.items()]

    def verify(self): return None
    def close(self): return None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_no_drift_when_hashes_match(tmp_path):
    (tmp_path / "a.py").write_text("def f(): pass\n")
    backend = _FakeBackend({"a.py": _sha("def f(): pass\n")})
    report = probe_staleness(str(tmp_path), backend, project="p")
    assert report.drifted_files == 0
    assert report.has_drift is False


def test_drift_counted_when_disk_differs(tmp_path):
    (tmp_path / "a.py").write_text("def f(): pass\n")
    backend = _FakeBackend({"a.py": "deadbeef"})
    report = probe_staleness(str(tmp_path), backend, project="p")
    assert report.drifted_files == 1
    assert report.has_drift is True


def test_drift_counts_file_only_on_disk(tmp_path):
    (tmp_path / "new.py").write_text("def g(): pass\n")
    backend = _FakeBackend({})  # nothing stored
    report = probe_staleness(str(tmp_path), backend, project="p")
    assert report.drifted_files == 1


def test_drift_counts_file_only_in_graph(tmp_path):
    backend = _FakeBackend({"gone.py": _sha("anything")})
    report = probe_staleness(str(tmp_path), backend, project="p")
    assert report.drifted_files == 1


def test_multiple_files_counted(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    backend = _FakeBackend({
        "a.py": "wronghash",         # drift
        "b.py": _sha("y = 2\n"),     # ok
    })
    report = probe_staleness(str(tmp_path), backend, project="p")
    assert report.drifted_files == 1
```

- [ ] **Step 2: Run, expect collection error**

- [ ] **Step 3: Implement `staleness.py`**

```python
"""Compare on-disk file hashes against stored Project content_hashes."""
from __future__ import annotations

import hashlib
import os

from livegraph.check.models import StalenessReport
from livegraph.discovery import discover_python_files
from livegraph.graph.backend import GraphBackend


def probe_staleness(
    root: str, backend: GraphBackend, project: str,
) -> StalenessReport:
    """Count files whose on-disk SHA-256 differs from the graph's
    stored content_hash, plus files present in one side only."""
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
        "RETURN f.path AS path, f.content_hash AS hash",
        project=project,
    )
    stored: dict[str, str | None] = {r["path"]: r.get("hash") for r in rows}

    on_disk: dict[str, str] = {}
    for rel in discover_python_files(root):
        abs_path = os.path.join(root, rel)
        with open(abs_path, "rb") as h:
            on_disk[rel] = hashlib.sha256(h.read()).hexdigest()

    drifted = 0
    for rel, disk_hash in on_disk.items():
        if stored.get(rel) != disk_hash:
            drifted += 1
    for rel in stored.keys():
        if rel not in on_disk:
            drifted += 1
    return StalenessReport(drifted_files=drifted)
```

- [ ] **Step 4: Run tests, expect 5 PASS**

- [ ] **Step 5: Commit**

```bash
git add livegraph/check/staleness.py tests/unit/test_check_staleness.py
git commit -m "feat(phase12): staleness probe (disk vs graph content_hash)"
```

---

## Task 4: Check adapters

**Files:**
- Create: `livegraph/check/checks.py`
- Test: `tests/unit/test_check_checks.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from typing import Any

from livegraph.check.checks import (
    check_churn, check_cycles, check_hubs, check_layering,
)
from livegraph.check.config import (
    ChurnConfig, CyclesConfig, HubsConfig, LayeringConfig,
)


class _FakeBackend:
    def __init__(self, responses: dict[str, list[dict[str, Any]]] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        for key, rows in self._responses.items():
            if key in cypher:
                return rows
        return []

    def verify(self): return None
    def close(self): return None


# ---- cycles --------------------------------------------------------

def test_cycles_disabled_is_skipped():
    r = check_cycles(CyclesConfig(enabled=False), _FakeBackend(), "p")
    assert r.status == "skipped"
    assert "disabled" in (r.reason or "")


def test_cycles_passes_when_no_cycles_found():
    backend = _FakeBackend({":IMPORTS": []})
    r = check_cycles(
        CyclesConfig(enabled=True, scope="module", max_cycles=0),
        backend, "p",
    )
    assert r.status == "passed"
    assert r.actual == 0
    assert r.threshold == 0


def test_cycles_fails_when_cycles_exceed_threshold():
    backend = _FakeBackend({":IMPORTS": [
        {"source": "a.py", "target": "b.py"},
        {"source": "b.py", "target": "a.py"},
    ]})
    r = check_cycles(
        CyclesConfig(enabled=True, scope="module", max_cycles=0),
        backend, "p",
    )
    assert r.status == "failed"
    assert r.actual == 1


# ---- layering ------------------------------------------------------

def test_layering_disabled_is_skipped():
    r = check_layering(LayeringConfig(enabled=False), _FakeBackend(), "p")
    assert r.status == "skipped"


def test_layering_passes_when_no_violations():
    backend = _FakeBackend({
        "(f:File)": [{"path": "web/a.py"}],
        ":IMPORTS": [],
        "CALLS": [],
    })
    r = check_layering(
        LayeringConfig(
            enabled=True,
            layers=({"name": "web", "patterns": ["web/**"]},),
        ),
        backend, "p",
    )
    assert r.status == "passed"


def test_layering_fails_when_violations_exceed_threshold():
    backend = _FakeBackend({
        "(f:File)": [
            {"path": "web/h.py"}, {"path": "domain/c.py"},
        ],
        ":IMPORTS": [
            {"from_file": "domain/c.py", "to_file": "web/h.py"},
        ],
        "CALLS": [],
    })
    r = check_layering(
        LayeringConfig(
            enabled=True, max_violations=0,
            layers=(
                {"name": "web", "patterns": ["web/**"]},
                {"name": "domain", "patterns": ["domain/**"]},
            ),
        ),
        backend, "p",
    )
    assert r.status == "failed"
    assert r.actual >= 1


# ---- churn ---------------------------------------------------------

def test_churn_disabled_is_skipped():
    r = check_churn(ChurnConfig(enabled=False), _FakeBackend(), "p")
    assert r.status == "skipped"


def test_churn_passes_when_no_symbol_exceeds_threshold():
    backend = _FakeBackend({
        "ORDER BY commit_count": [
            {"qualified_name": "pkg.a", "file": "pkg/a.py",
             "kind": "function", "commit_count": 3,
             "unique_authors": 1,
             "first_changed": "2026-05-01", "last_changed": "2026-05-29"},
        ],
    })
    r = check_churn(
        ChurnConfig(enabled=True, window_days=30, hot_files_threshold=10),
        backend, "p",
    )
    assert r.status == "passed"
    assert r.actual == 0


def test_churn_fails_when_hotspot_above_threshold():
    backend = _FakeBackend({
        "ORDER BY commit_count": [
            {"qualified_name": "pkg.hot", "file": "pkg/hot.py",
             "kind": "function", "commit_count": 25,
             "unique_authors": 4,
             "first_changed": "2026-05-01", "last_changed": "2026-05-29"},
        ],
    })
    r = check_churn(
        ChurnConfig(enabled=True, hot_files_threshold=10),
        backend, "p",
    )
    assert r.status == "failed"
    assert r.actual == 1


def test_churn_ignore_glob_excludes_matching_symbols():
    backend = _FakeBackend({
        "ORDER BY commit_count": [
            {"qualified_name": "tests.hot", "file": "tests/hot.py",
             "kind": "function", "commit_count": 99,
             "unique_authors": 4,
             "first_changed": "2026-05-01", "last_changed": "2026-05-29"},
        ],
    })
    r = check_churn(
        ChurnConfig(enabled=True, hot_files_threshold=10,
                    ignore=("tests/**",)),
        backend, "p",
    )
    assert r.status == "passed"
    assert r.actual == 0


# ---- hubs ----------------------------------------------------------

def test_hubs_disabled_is_skipped():
    r = check_hubs(HubsConfig(enabled=False), _FakeBackend(), "p")
    assert r.status == "skipped"


def test_hubs_passes_when_no_hubs_above_threshold():
    backend = _FakeBackend({"_HUBS_": []})
    r = check_hubs(
        HubsConfig(enabled=True, min_fanin=25, max_hubs=0),
        backend, "p",
    )
    assert r.status == "passed"


def test_hubs_fails_when_above_threshold():
    backend = _FakeBackend({"ORDER BY in_callers": [
        {"qualified_name": "pkg.util.normalize", "kind": "function",
         "file": "pkg/util.py", "in_callers": 47, "out_callees": 3},
    ]})
    r = check_hubs(
        HubsConfig(enabled=True, min_fanin=25, max_hubs=0),
        backend, "p",
    )
    assert r.status == "failed"
    assert r.actual == 1
```

- [ ] **Step 2: Run, expect collection error**

- [ ] **Step 3: Implement `checks.py`**

```python
"""Per-check adapters: wrap Phase 10/11 functions into CheckResult."""
from __future__ import annotations

import fnmatch
from typing import Any

from livegraph.check.config import (
    ChurnConfig, CyclesConfig, HubsConfig, LayeringConfig,
)
from livegraph.check.models import CheckResult
from livegraph.graph.backend import GraphBackend
from livegraph.mcp.tools_architecture import (
    find_cycles, hubs as hubs_tool, layering_violations,
)
from livegraph.mcp.tools_history import top_churn


def _matches_any(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def check_cycles(
    cfg: CyclesConfig, backend: GraphBackend, project: str,
) -> CheckResult:
    if not cfg.enabled:
        return CheckResult.skipped("cycles", "disabled in config")
    out = find_cycles(
        backend, project, scope=cfg.scope, provenance=cfg.provenance,
        min_size=cfg.min_size,
        limit=max(cfg.max_cycles + 10, 20),
    )
    if out["warning"]:
        return CheckResult.error("cycles", out["warning"])
    cycles = list(out["cycles"])
    actual = len(cycles)
    status = "passed" if actual <= cfg.max_cycles else "failed"
    return CheckResult(
        check="cycles", status=status,
        actual=actual, threshold=cfg.max_cycles,
        items=tuple(cycles),
    )


def check_layering(
    cfg: LayeringConfig, backend: GraphBackend, project: str,
) -> CheckResult:
    if not cfg.enabled:
        return CheckResult.skipped("layering", "disabled in config")
    if not cfg.layers:
        return CheckResult.error(
            "layering", "no layers defined in config",
        )
    out = layering_violations(
        backend, project,
        layers=[dict(l) for l in cfg.layers],
        edge_kind=cfg.edge_kind,
        limit=max(cfg.max_violations + 50, 50),
    )
    if out.get("warning"):
        return CheckResult.error("layering", out["warning"])
    violations = list(out["violations"])
    actual = len(violations)
    status = "passed" if actual <= cfg.max_violations else "failed"
    return CheckResult(
        check="layering", status=status,
        actual=actual, threshold=cfg.max_violations,
        items=tuple(violations),
    )


def check_churn(
    cfg: ChurnConfig, backend: GraphBackend, project: str,
) -> CheckResult:
    if not cfg.enabled:
        return CheckResult.skipped("churn", "disabled in config")
    out = top_churn(
        backend, project,
        window_days=cfg.window_days, limit=100, kind="any",
    )
    if out.get("warning"):
        return CheckResult.error("churn", out["warning"])
    hotspots = []
    for r in out["results"]:
        if r.get("commit_count", 0) <= cfg.hot_files_threshold:
            continue
        file = r.get("file") or ""
        if _matches_any(file, cfg.ignore):
            continue
        hotspots.append(r)
    actual = len(hotspots)
    status = "passed" if actual == 0 else "failed"
    return CheckResult(
        check="churn", status=status,
        actual=actual, threshold=cfg.hot_files_threshold,
        items=tuple(hotspots),
    )


def check_hubs(
    cfg: HubsConfig, backend: GraphBackend, project: str,
) -> CheckResult:
    if not cfg.enabled:
        return CheckResult.skipped("hubs", "disabled in config")
    out = hubs_tool(
        backend, project,
        kind=cfg.kind, min_fanin=cfg.min_fanin,
        limit=max(cfg.max_hubs + 10, 20),
    )
    if out.get("warning"):
        return CheckResult.error("hubs", out["warning"])
    results = list(out["results"])
    actual = len(results)
    status = "passed" if actual <= cfg.max_hubs else "failed"
    return CheckResult(
        check="hubs", status=status,
        actual=actual, threshold=cfg.max_hubs,
        items=tuple(results),
    )
```

- [ ] **Step 4: Run tests, expect all PASS**

- [ ] **Step 5: Commit**

```bash
git add livegraph/check/checks.py tests/unit/test_check_checks.py
git commit -m "feat(phase12): per-check adapters (cycles/layering/churn/hubs)"
```

---

## Task 5: Runner

**Files:**
- Create: `livegraph/check/runner.py`
- Test: `tests/unit/test_check_runner.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from livegraph.check.config import (
    CheckConfig, ChurnConfig, CyclesConfig, HubsConfig, LayeringConfig,
)
from livegraph.check.models import CheckResult, StalenessReport
from livegraph.check.runner import compute_exit_code, run_checks


class _StubBackend:
    def execute(self, cypher, **params): return []
    def verify(self): return None
    def close(self): return None


def _cfg(**overrides) -> CheckConfig:
    base = dict(
        project="p",
        cycles=CyclesConfig(enabled=False),
        layering=LayeringConfig(enabled=False),
        churn=ChurnConfig(enabled=False),
        hubs=HubsConfig(enabled=False),
    )
    base.update(overrides)
    return CheckConfig(**base)


def test_run_checks_all_disabled_returns_skipped_for_each():
    backend = _StubBackend()
    report = run_checks(
        _cfg(), backend, staleness=StalenessReport(drifted_files=0),
    )
    assert report.skipped == 4
    assert report.passed == 0
    assert report.failed == 0


def test_run_checks_passes_when_threshold_met(monkeypatch):
    monkeypatch.setattr(
        "livegraph.check.runner.check_cycles",
        lambda cfg, backend, project: CheckResult(
            check="cycles", status="passed", actual=0, threshold=0,
        ),
    )
    cfg = _cfg(cycles=CyclesConfig(enabled=True))
    report = run_checks(cfg, _StubBackend(),
                       staleness=StalenessReport(drifted_files=0))
    assert report.passed == 1
    assert report.failed == 0


def test_fail_fast_stops_after_first_failure(monkeypatch):
    calls = []
    def stub(check_name, status):
        def fn(cfg, backend, project):
            calls.append(check_name)
            return CheckResult(check=check_name, status=status,
                               actual=1 if status == "failed" else 0,
                               threshold=0)
        return fn

    monkeypatch.setattr("livegraph.check.runner.check_cycles",
                        stub("cycles", "failed"))
    monkeypatch.setattr("livegraph.check.runner.check_layering",
                        stub("layering", "passed"))
    monkeypatch.setattr("livegraph.check.runner.check_churn",
                        stub("churn", "passed"))
    monkeypatch.setattr("livegraph.check.runner.check_hubs",
                        stub("hubs", "passed"))
    cfg = _cfg(
        cycles=CyclesConfig(enabled=True),
        layering=LayeringConfig(enabled=True),
        churn=ChurnConfig(enabled=True),
        hubs=HubsConfig(enabled=True),
    )
    report = run_checks(
        cfg, _StubBackend(),
        staleness=StalenessReport(drifted_files=0),
        fail_fast=True,
    )
    assert calls == ["cycles"]
    assert report.failed == 1


def test_compute_exit_code_zero_on_all_passed():
    report_results = (
        CheckResult(check="cycles", status="passed"),
        CheckResult(check="layering", status="skipped"),
    )
    code = compute_exit_code(
        results=report_results,
        staleness=StalenessReport(drifted_files=0),
        strict=False,
    )
    assert code == 0


def test_compute_exit_code_one_on_failed():
    results = (
        CheckResult(check="cycles", status="failed", actual=1, threshold=0),
    )
    code = compute_exit_code(
        results, StalenessReport(0), strict=False,
    )
    assert code == 1


def test_compute_exit_code_two_on_strict_with_drift():
    results = (
        CheckResult(check="cycles", status="passed"),
    )
    code = compute_exit_code(
        results, StalenessReport(drifted_files=3), strict=True,
    )
    assert code == 2


def test_compute_exit_code_one_on_drift_without_strict():
    results = (
        CheckResult(check="cycles", status="passed"),
    )
    code = compute_exit_code(
        results, StalenessReport(drifted_files=3), strict=False,
    )
    assert code == 0
```

- [ ] **Step 2: Run, expect collection error**

- [ ] **Step 3: Implement `runner.py`**

```python
"""Orchestrator + exit-code logic for `livegraph check`."""
from __future__ import annotations

from livegraph.check.checks import (
    check_churn, check_cycles, check_hubs, check_layering,
)
from livegraph.check.config import CheckConfig
from livegraph.check.models import CheckResult, Report, StalenessReport
from livegraph.graph.backend import GraphBackend


def run_checks(
    config: CheckConfig,
    backend: GraphBackend,
    staleness: StalenessReport,
    fail_fast: bool = False,
) -> Report:
    """Run every check in a fixed order; honor fail_fast."""
    results: list[CheckResult] = []

    # Order matters for fail-fast diagnostics: cheapest -> noisiest.
    runners = [
        ("cycles", lambda: check_cycles(config.cycles, backend, config.project)),
        ("layering", lambda: check_layering(config.layering, backend, config.project)),
        ("churn", lambda: check_churn(config.churn, backend, config.project)),
        ("hubs", lambda: check_hubs(config.hubs, backend, config.project)),
    ]
    for _, fn in runners:
        r = fn()
        results.append(r)
        if fail_fast and r.status in ("failed", "error"):
            break

    return Report(
        project=config.project,
        staleness=staleness,
        results=tuple(results),
        warnings=config.warnings,
    )


def compute_exit_code(
    results: tuple[CheckResult, ...],
    staleness: StalenessReport,
    strict: bool,
) -> int:
    """0 = all passed, 1 = at least one failed/error, 2 = strict+stale."""
    if strict and staleness.has_drift:
        return 2
    if any(r.status in ("failed", "error") for r in results):
        return 1
    return 0
```

- [ ] **Step 4: Run tests, expect all PASS**

- [ ] **Step 5: Commit**

```bash
git add livegraph/check/runner.py tests/unit/test_check_runner.py
git commit -m "feat(phase12): runner + exit-code logic"
```

---

## Task 6: Report renderers

**Files:**
- Create: `livegraph/check/report.py`
- Test: `tests/unit/test_check_report.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import json

from livegraph.check.models import CheckResult, Report, StalenessReport
from livegraph.check.report import render_json, render_text


def _r(
    project="myproj", drifted=0, results=(), warnings=(),
) -> Report:
    return Report(
        project=project,
        staleness=StalenessReport(drifted_files=drifted),
        results=tuple(results),
        warnings=tuple(warnings),
    )


def test_text_renders_pass_and_fail():
    report = _r(results=(
        CheckResult(check="cycles", status="passed",
                    actual=0, threshold=0),
        CheckResult(check="layering", status="failed",
                    actual=4, threshold=0,
                    items=tuple({"from_file": f"a{i}.py",
                                 "to_file": f"b{i}.py",
                                 "edge_kind": "imports"}
                                 for i in range(4))),
    ))
    text = render_text(report)
    assert "[PASS] cycles" in text
    assert "[FAIL] layering" in text
    assert "4 / max 0" in text
    assert "a0.py" in text
    assert "Summary:" in text


def test_text_truncates_items_with_count():
    items = tuple({"from_file": f"a{i}.py", "to_file": "b.py",
                   "edge_kind": "imports"} for i in range(10))
    report = _r(results=(
        CheckResult(check="layering", status="failed",
                    actual=10, threshold=0, items=items),
    ))
    text = render_text(report)
    # 5 lines shown, then "...5 more"
    assert "... 5 more" in text


def test_text_shows_staleness_warning():
    report = _r(drifted=3)
    text = render_text(report)
    assert "staleness" in text.lower()
    assert "3" in text


def test_text_renders_skipped_check():
    report = _r(results=(
        CheckResult(check="hubs", status="skipped",
                    reason="disabled in config"),
    ))
    text = render_text(report)
    assert "[SKIP] hubs" in text


def test_json_renders_all_fields():
    report = _r(
        drifted=2,
        results=(
            CheckResult(check="cycles", status="passed",
                        actual=0, threshold=0),
        ),
        warnings=("unknown_thing",),
    )
    parsed = json.loads(render_json(report, exit_code=0))
    assert parsed["project"] == "myproj"
    assert parsed["graph_staleness"]["drifted_files"] == 2
    assert parsed["results"][0]["check"] == "cycles"
    assert parsed["results"][0]["status"] == "passed"
    assert parsed["summary"]["passed"] == 1
    assert parsed["exit_code"] == 0
    assert parsed["warnings"] == ["unknown_thing"]


def test_json_includes_full_items_no_truncation():
    items = tuple({"id": i} for i in range(50))
    report = _r(results=(
        CheckResult(check="x", status="failed",
                    actual=50, threshold=0, items=items),
    ))
    parsed = json.loads(render_json(report, exit_code=1))
    assert len(parsed["results"][0]["items"]) == 50
```

- [ ] **Step 2: Run, expect collection error**

- [ ] **Step 3: Implement `report.py`**

```python
"""Render a Report to text or JSON for the `livegraph check` CLI."""
from __future__ import annotations

import json
from typing import Any

from livegraph.check.models import Report

_TEXT_ITEM_CAP = 5


def render_text(report: Report) -> str:
    lines: list[str] = []
    lines.append(f"Project: {report.project}")
    if report.staleness.has_drift:
        lines.append(
            f"Graph staleness: {report.staleness.drifted_files} "
            f"files drifted from disk (run `livegraph update`)"
        )
    lines.append("")
    for r in report.results:
        if r.status == "passed":
            lines.append(
                f"[PASS] {r.check:<24} {r.actual} / max {r.threshold}"
            )
        elif r.status == "failed":
            lines.append(
                f"[FAIL] {r.check:<24} {r.actual} / max {r.threshold}"
            )
        elif r.status == "skipped":
            lines.append(
                f"[SKIP] {r.check:<24} {r.reason or ''}"
            )
        else:  # error
            lines.append(
                f"[ERR ] {r.check:<24} {r.reason or ''}"
            )
        if r.items:
            shown = r.items[:_TEXT_ITEM_CAP]
            for item in shown:
                lines.append(f"  {_format_item(item)}")
            extra = len(r.items) - len(shown)
            if extra > 0:
                lines.append(f"  ... {extra} more")
    lines.append("")
    warns = (
        f", warnings: {len(report.warnings) + (1 if report.staleness.has_drift else 0)}"
        if report.warnings or report.staleness.has_drift
        else ""
    )
    lines.append(
        f"Summary: {report.passed} passed, {report.failed} failed, "
        f"{report.skipped} skipped{warns}"
    )
    return "\n".join(lines)


def _format_item(item: dict[str, Any]) -> str:
    if "from_file" in item and "to_file" in item:
        kind = item.get("edge_kind", "")
        return f"{item['from_file']} -> {item['to_file']} ({kind})"
    if "qualified_name" in item:
        extra = []
        if "in_callers" in item:
            extra.append(f"in={item['in_callers']}")
        if "commit_count" in item:
            extra.append(f"commits={item['commit_count']}")
        suffix = f" [{', '.join(extra)}]" if extra else ""
        return f"{item['qualified_name']}{suffix}"
    if "nodes" in item:
        nodes = item["nodes"]
        head = ", ".join(nodes[:3])
        tail = "" if len(nodes) <= 3 else f" + {len(nodes) - 3} more"
        return f"size={item.get('size', len(nodes))}: {head}{tail}"
    return str(item)


def render_json(report: Report, exit_code: int) -> str:
    payload = {
        "project": report.project,
        "graph_staleness": {
            "drifted_files": report.staleness.drifted_files,
        },
        "results": [
            _result_dict(r) for r in report.results
        ],
        "summary": {
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
        },
        "warnings": list(report.warnings),
        "exit_code": exit_code,
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def _result_dict(r) -> dict[str, Any]:
    out: dict[str, Any] = {"check": r.check, "status": r.status}
    if r.status in ("passed", "failed"):
        out["actual"] = r.actual
        out["threshold"] = r.threshold
    if r.reason is not None:
        out["reason"] = r.reason
    out["items"] = [dict(i) for i in r.items]
    return out
```

- [ ] **Step 4: Run tests, expect all PASS**

- [ ] **Step 5: Commit**

```bash
git add livegraph/check/report.py tests/unit/test_check_report.py
git commit -m "feat(phase12): text + JSON report renderers"
```

---

## Task 7: CLI `livegraph check`

**Files:**
- Modify: `livegraph/cli.py`
- Test: `tests/unit/test_cli_check.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from livegraph.cli import app

runner = CliRunner()


def _write_cfg(tmp_path: Path, text: str = '[project]\nname = "p"\n') -> Path:
    p = tmp_path / ".livegraph.toml"
    p.write_text(text)
    return p


def test_check_help():
    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.stdout
    assert "--format" in result.stdout
    assert "--strict" in result.stdout
    assert "--fail-fast" in result.stdout


def test_check_missing_config_exits_2(tmp_path):
    result = runner.invoke(
        app, ["check", "--config", str(tmp_path / "nope.toml")],
    )
    assert result.exit_code == 2


def test_check_invalid_format_exits_2(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    result = runner.invoke(
        app, ["check", "--config", str(cfg), "--format", "yaml"],
    )
    assert result.exit_code == 2


def test_check_all_disabled_returns_zero(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    from livegraph import cli as cli_mod
    from livegraph.check.models import StalenessReport

    def fake_make_backend():
        b = type("B", (), {})()
        b.verify = lambda: None
        b.close = lambda: None
        return b

    monkeypatch.setattr(cli_mod, "_make_backend", fake_make_backend)
    monkeypatch.setattr(
        "livegraph.check.staleness.probe_staleness",
        lambda *a, **kw: StalenessReport(drifted_files=0),
    )
    result = runner.invoke(
        app, ["check", "--config", str(cfg), str(tmp_path)],
    )
    assert result.exit_code == 0
```

- [ ] **Step 2: Run, expect failures**

- [ ] **Step 3: Add the `check` command to `livegraph/cli.py`**

Near the top, add to imports:

```python
import sys
from pathlib import Path

from livegraph.check.config import ConfigError, load_config
from livegraph.check.report import render_json, render_text
from livegraph.check.runner import compute_exit_code, run_checks
from livegraph.check.staleness import probe_staleness
```

At the bottom of `cli.py`, after the `ingest-history` command, add:

```python
def _find_default_config(start: Path) -> Path | None:
    cur = start.resolve()
    for parent in [cur, *cur.parents]:
        candidate = parent / ".livegraph.toml"
        if candidate.exists():
            return candidate
    return None


@app.command()
def check(
    path: str = typer.Argument(
        None,
        help="Project root (defaults to the Project's stored root_path)",
    ),
    config: str = typer.Option(
        None, "--config",
        help="Path to .livegraph.toml (default: search up from CWD)",
    ),
    format: str = typer.Option(
        "text", "--format",
        help="Output format: text or json",
    ),
    fail_fast: bool = typer.Option(
        False, "--fail-fast",
        help="Stop at the first failing check",
    ),
    strict: bool = typer.Option(
        False, "--strict",
        help="Promote staleness drift to exit code 2",
    ),
) -> None:
    """CI mode: run config-driven checks against the graph."""
    if format not in ("text", "json"):
        typer.echo(f"unknown --format: {format!r}", err=True)
        raise typer.Exit(code=2)

    if config:
        cfg_path = Path(config)
    else:
        cfg_path = _find_default_config(Path.cwd())
        if cfg_path is None:
            typer.echo(
                "no .livegraph.toml found; pass --config or create one",
                err=True,
            )
            raise typer.Exit(code=2)

    try:
        cfg = load_config(cfg_path)
    except ConfigError as exc:
        typer.echo(f"config error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    backend = _make_backend()
    try:
        backend.verify()
    except ConnectionError as exc:
        typer.echo(f"Neo4j unreachable: {exc}", err=True)
        backend.close()
        raise typer.Exit(code=2) from exc

    try:
        resolved_root = path or _resolve_root_path(backend, cfg.project)
        if not resolved_root:
            typer.echo(
                f"Project {cfg.project!r} not in graph; "
                f"run `livegraph build` first.",
                err=True,
            )
            raise typer.Exit(code=2)

        staleness = probe_staleness(resolved_root, backend, cfg.project)
        report = run_checks(cfg, backend, staleness=staleness,
                            fail_fast=fail_fast)
        exit_code = compute_exit_code(
            report.results, staleness, strict=strict,
        )

        if format == "json":
            sys.stdout.write(render_json(report, exit_code=exit_code))
            sys.stdout.write("\n")
        else:
            typer.echo(render_text(report))
        raise typer.Exit(code=exit_code)
    finally:
        backend.close()
```

- [ ] **Step 4: Run tests, expect 4 PASS**

```
.venv/bin/python -m pytest tests/unit/test_cli_check.py -v
```

Then full CLI test suite for regressions:

```
.venv/bin/python -m pytest tests/unit/test_cli.py tests/unit/test_cli_watch.py tests/unit/test_cli_ingest_history.py tests/unit/test_cli_check.py -v
```

- [ ] **Step 5: Commit**

```bash
git add livegraph/cli.py tests/unit/test_cli_check.py
git commit -m "feat(phase12): livegraph check CLI command"
```

---

## Task 8: Integration test

**Files:**
- Create: `tests/integration/test_check_integration.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end: real Neo4j + .livegraph.toml + livegraph check."""
from __future__ import annotations

from pathlib import Path

import pytest

from livegraph.check.config import load_config
from livegraph.check.runner import compute_exit_code, run_checks
from livegraph.check.staleness import probe_staleness

pytestmark = pytest.mark.integration


@pytest.fixture()
def check_project(neo4j_backend, tmp_path):
    """Synthetic graph: 3 files with one import cycle and one layering
    violation. Also write a .livegraph.toml that catches them."""
    backend = neo4j_backend
    project = "check_test"

    backend.execute(
        "MERGE (p:Project {name: $project}) "
        "WITH p UNWIND $paths AS path "
        "MERGE (f:File {path: path, content_hash: 'h_' + path}) "
        "MERGE (p)-[:CONTAINS]->(f)",
        project=project,
        paths=["web/handlers.py", "domain/calc.py", "infra/db.py"],
    )
    # Import cycle web <-> domain
    backend.execute(
        "MATCH (a:File {path: 'web/handlers.py'}), "
        "      (b:File {path: 'domain/calc.py'}) "
        "MERGE (a)-[:IMPORTS]->(b) "
        "MERGE (b)-[:IMPORTS]->(a)",
    )

    cfg = tmp_path / ".livegraph.toml"
    cfg.write_text(f"""
[project]
name = "{project}"

[checks.cycles]
enabled = true
scope = "module"
max_cycles = 0

[checks.layering]
enabled = true
edge_kind = "imports"
max_violations = 0
layers = [
    {{ name = "web", patterns = ["web/**"] }},
    {{ name = "domain", patterns = ["domain/**"] }},
    {{ name = "infra", patterns = ["infra/**"] }},
]
""")
    # Create the files on disk so the staleness probe doesn't drown the
    # test in spurious "missing on disk" drift.
    for f in ["web/handlers.py", "domain/calc.py", "infra/db.py"]:
        target = tmp_path / f
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# placeholder\n")
    return backend, project, tmp_path, cfg


def test_full_run_reports_cycles_and_layering(check_project):
    backend, project, root, cfg_path = check_project
    cfg = load_config(cfg_path)
    staleness = probe_staleness(str(root), backend, project)
    report = run_checks(cfg, backend, staleness=staleness)
    by_check = {r.check: r for r in report.results}
    assert by_check["cycles"].status == "failed"
    assert by_check["layering"].status == "failed"
    code = compute_exit_code(report.results, staleness, strict=False)
    assert code == 1


def test_strict_promotes_staleness_to_exit_2(check_project):
    backend, project, root, cfg_path = check_project
    cfg = load_config(cfg_path)
    # Synthesize drift by writing to one file's content (the stored
    # hash is 'h_...' which won't match real SHA-256).
    (root / "web" / "handlers.py").write_text("def x(): pass\n")
    staleness = probe_staleness(str(root), backend, project)
    assert staleness.has_drift
    report = run_checks(cfg, backend, staleness=staleness)
    code = compute_exit_code(report.results, staleness, strict=True)
    assert code == 2
```

- [ ] **Step 2: Run with Neo4j up**

```
.venv/bin/python -m pytest tests/integration/test_check_integration.py -v -m integration
```

If the cycles check passes when it should fail, double-check that the synthetic graph's `:IMPORTS` edges were created (run `MATCH (a)-[r:IMPORTS]->(b) RETURN a.path, b.path` against Neo4j) — and that nothing in the staleness probe is suppressing it.

- [ ] **Step 3: Run the full suite for regressions**

```
.venv/bin/python -m pytest -q
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_check_integration.py
git commit -m "test(phase12): end-to-end check against real Neo4j"
```

---

## Task 9: README section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a new section at the end of `README.md`**

```markdown

## CI mode (`livegraph check`)

Phase 12 turns the existing analysis surface into something CI can
enforce. Drop a `.livegraph.toml` at the project root:

```toml
[project]
name = "myproj"

[checks.cycles]
enabled = true
scope = "module"
max_cycles = 0

[checks.layering]
enabled = true
max_violations = 0
layers = [
    { name = "web",    patterns = ["web/**", "api/**"] },
    { name = "domain", patterns = ["domain/**"] },
    { name = "infra",  patterns = ["infra/**", "db/**"] },
]

[checks.churn]
enabled = true
window_days = 30
hot_files_threshold = 10
ignore = ["tests/**"]

[checks.hubs]
enabled = false
min_fanin = 25
max_hubs = 0
```

Then in CI:

```bash
livegraph update          # refresh the graph
livegraph check           # run all enabled checks
```

Exit codes: `0` = pass, `1` = at least one check failed, `2` = config
or environment error (or, with `--strict`, the graph is stale vs disk).
Output is human-readable by default; pass `--format json` for a
machine-readable payload, `--fail-fast` to stop at the first failing
check.

Each check wraps an existing MCP tool — `find_cycles`,
`layering_violations`, `top_churn`, `hubs` — so the agent's "show me"
question and CI's "fail the build" question read the same graph data.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(phase12): README section for CI mode"
```

---

## Acceptance gate (manual, before PR)

- [ ] `.venv/bin/python -m pytest -q` → all tests pass.
- [ ] `.venv/bin/python -m ruff check .` → no new errors compared to main.
- [ ] Manual smoke: drop a `.livegraph.toml` in livegraph's own repo with `cycles` enabled, run `livegraph check` and confirm the text output is sensible.
- [ ] Manual smoke (`--strict`): edit a `.py` file without running `livegraph update`, then `livegraph check --strict` — confirm exit code 2.
- [ ] Manual smoke (`--format json`): pipe to `jq` and verify the shape matches the spec.
