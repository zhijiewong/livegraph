# livegraph Phase 8 — `livegraph watch` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a foreground `livegraph watch` CLI that mirrors `*.py` edits into the Neo4j graph within ~500ms of save, with optional live embedding refresh.

**Architecture:** New `livegraph/watch/` package with three units — `watcher.py` (watchdog adapter + filtering), `debouncer.py` (event coalescing with a clock seam), `loop.py` (orchestrator + error handling + backoff). Reuses Phase 5's reingest logic via a new explicit-path entry point `update_files`, and Phase 7's `embed_project` for the optional `--embed` step.

**Tech Stack:** Python 3.12+, `watchdog>=4.0`, existing `livegraph.incremental.reingest_files`, existing `livegraph.semantic.embed.embed_project`, Typer.

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add `watchdog>=4.0` to runtime deps. |
| `livegraph/config.py` | Modify | Add `livegraph_watch_debounce_ms: int = 300`. |
| `livegraph/watch/__init__.py` | Create | Package marker; re-export public types. |
| `livegraph/watch/events.py` | Create | `ChangeEvent`, `ChangeBatch` dataclasses (shared types, no I/O). |
| `livegraph/watch/debouncer.py` | Create | `Debouncer` — pulls events off a queue, coalesces into batches. Clock injected. |
| `livegraph/watch/filters.py` | Create | `PathFilter` — `.py`-only, gitignore, builtin ignores, user `--ignore`. Pure functions. |
| `livegraph/watch/watcher.py` | Create | `Watcher` — `watchdog.Observer` adapter that uses `PathFilter` and pushes `ChangeEvent`s onto a queue. |
| `livegraph/watch/loop.py` | Create | `run_loop(...)` — main orchestrator, calls `update_files` + optional `embed_project`, owns backoff. |
| `livegraph/incremental.py` | Modify | Add `update_files(root, backend, project, paths)` entry point reusing existing reingest internals. |
| `livegraph/cli.py` | Modify | New `watch` subcommand wiring it all together. |
| `tests/unit/test_watch_events.py` | Create | Coalescing-rule unit tests against `ChangeBatch` merging. |
| `tests/unit/test_watch_debouncer.py` | Create | Debouncer with a fake clock. |
| `tests/unit/test_watch_filters.py` | Create | Path filtering (`.py`, gitignore, builtin, `--ignore`). |
| `tests/unit/test_watch_loop.py` | Create | Loop with fake backend/debouncer/provider. |
| `tests/unit/test_incremental_update_files.py` | Create | `update_files` correctness (mod/del, runtime preservation). |
| `tests/unit/test_cli_watch.py` | Create | Flag parsing, `--embed` without `[semantic]`. |
| `tests/integration/test_watch_integration.py` | Create | Real watchdog + real Neo4j end-to-end. |
| `README.md` | Modify | Add a "Watch mode" section. |

Total: 8 new source files, 3 modified; 7 new test files, 1 modified doc.

---

## Task 1: Dependency + config knob

**Files:**
- Modify: `pyproject.toml`
- Modify: `livegraph/config.py`
- Test: `tests/unit/test_config.py` (extend existing)

- [ ] **Step 1: Write the failing test**

Open `tests/unit/test_config.py` and add at the bottom:

```python
def test_settings_default_watch_debounce_ms():
    from livegraph.config import Settings
    s = Settings()
    assert s.livegraph_watch_debounce_ms == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py::test_settings_default_watch_debounce_ms -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'livegraph_watch_debounce_ms'`.

- [ ] **Step 3: Add the field**

In `livegraph/config.py`, alongside the other `livegraph_*` fields on `Settings`, add:

```python
    livegraph_watch_debounce_ms: int = 300
```

- [ ] **Step 4: Add watchdog dependency**

In `pyproject.toml`, in the `[project]` `dependencies = [...]` list, add a line:

```
"watchdog>=4.0",
```

- [ ] **Step 5: Install + run test**

Run:
```
uv sync
uv run pytest tests/unit/test_config.py -v
```
Expected: all config tests PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock livegraph/config.py tests/unit/test_config.py
git commit -m "feat(phase8): watchdog dep + watch debounce config knob"
```

---

## Task 2: `ChangeEvent` + `ChangeBatch` types and coalescing rules

**Files:**
- Create: `livegraph/watch/__init__.py`
- Create: `livegraph/watch/events.py`
- Test: `tests/unit/test_watch_events.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_watch_events.py`:

```python
from pathlib import Path

from livegraph.watch.events import ChangeBatch, ChangeEvent


def E(kind, p):
    return ChangeEvent(kind=kind, path=Path(p))


def test_empty_batch_is_empty():
    b = ChangeBatch.empty()
    assert b.modified == set()
    assert b.deleted == set()
    assert b.is_empty()


def test_modified_plus_modified_collapses():
    b = ChangeBatch.empty().merge(E("modified", "a.py")).merge(E("modified", "a.py"))
    assert b.modified == {Path("a.py")}
    assert b.deleted == set()


def test_created_treated_as_modified():
    b = ChangeBatch.empty().merge(E("created", "a.py"))
    assert b.modified == {Path("a.py")}


def test_created_then_deleted_cancels():
    b = (
        ChangeBatch.empty()
        .merge(E("created", "a.py"))
        .merge(E("deleted", "a.py"))
    )
    assert b.is_empty()


def test_modified_then_deleted_becomes_deleted():
    b = (
        ChangeBatch.empty()
        .merge(E("modified", "a.py"))
        .merge(E("deleted", "a.py"))
    )
    assert b.modified == set()
    assert b.deleted == {Path("a.py")}


def test_deleted_then_created_becomes_modified():
    b = (
        ChangeBatch.empty()
        .merge(E("deleted", "a.py"))
        .merge(E("created", "a.py"))
    )
    assert b.modified == {Path("a.py")}
    assert b.deleted == set()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch_events.py -v`
Expected: collection error (`ModuleNotFoundError: livegraph.watch`).

- [ ] **Step 3: Create the package + types**

Create `livegraph/watch/__init__.py`:

```python
from livegraph.watch.events import ChangeBatch, ChangeEvent

__all__ = ["ChangeBatch", "ChangeEvent"]
```

Create `livegraph/watch/events.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

EventKind = Literal["modified", "created", "deleted"]


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    kind: EventKind
    path: Path


@dataclass(frozen=True, slots=True)
class ChangeBatch:
    modified: frozenset[Path] = field(default_factory=frozenset)
    deleted: frozenset[Path] = field(default_factory=frozenset)

    @classmethod
    def empty(cls) -> "ChangeBatch":
        return cls()

    def is_empty(self) -> bool:
        return not self.modified and not self.deleted

    def merge(self, event: ChangeEvent) -> "ChangeBatch":
        modified = set(self.modified)
        deleted = set(self.deleted)
        p = event.path
        if event.kind in ("modified", "created"):
            if p in deleted:
                deleted.discard(p)
                modified.add(p)
            else:
                modified.add(p)
        elif event.kind == "deleted":
            if p in modified:
                modified.discard(p)
                if p not in deleted and p in self.modified and p not in self._created_in(event):
                    deleted.add(p)
                else:
                    # created-then-deleted in the same batch: cancel
                    pass
            else:
                deleted.add(p)
        return ChangeBatch(modified=frozenset(modified), deleted=frozenset(deleted))

    def _created_in(self, _event: ChangeEvent) -> set[Path]:
        # Stub used by merge() so its branching reads cleanly.
        return set()
```

Wait — the cancel rule needs us to remember "was this path *created* in this batch?" The simple dataclass above can't track that. Replace `events.py` with this version that keeps a private `_created` set:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

EventKind = Literal["modified", "created", "deleted"]


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    kind: EventKind
    path: Path


@dataclass(frozen=True, slots=True)
class ChangeBatch:
    modified: frozenset[Path] = field(default_factory=frozenset)
    deleted: frozenset[Path] = field(default_factory=frozenset)
    _created: frozenset[Path] = field(default_factory=frozenset)

    @classmethod
    def empty(cls) -> "ChangeBatch":
        return cls()

    def is_empty(self) -> bool:
        return not self.modified and not self.deleted

    def merge(self, event: ChangeEvent) -> "ChangeBatch":
        modified = set(self.modified)
        deleted = set(self.deleted)
        created = set(self._created)
        p = event.path

        if event.kind == "created":
            deleted.discard(p)
            modified.add(p)
            created.add(p)
        elif event.kind == "modified":
            deleted.discard(p)
            modified.add(p)
        elif event.kind == "deleted":
            if p in created:
                # created-then-deleted in the same batch: cancel
                modified.discard(p)
                created.discard(p)
            else:
                modified.discard(p)
                deleted.add(p)

        return ChangeBatch(
            modified=frozenset(modified),
            deleted=frozenset(deleted),
            _created=frozenset(created),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_watch_events.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add livegraph/watch/__init__.py livegraph/watch/events.py tests/unit/test_watch_events.py
git commit -m "feat(phase8): ChangeEvent + ChangeBatch with coalescing rules"
```

---

## Task 3: Debouncer with a clock seam

**Files:**
- Create: `livegraph/watch/debouncer.py`
- Test: `tests/unit/test_watch_debouncer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_watch_debouncer.py`:

```python
from __future__ import annotations

import queue
from pathlib import Path

from livegraph.watch.debouncer import Debouncer
from livegraph.watch.events import ChangeEvent


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_empty_queue_returns_none_after_timeout():
    q: queue.Queue[ChangeEvent] = queue.Queue()
    clock = FakeClock()
    d = Debouncer(q, window_ms=300, clock=clock.now, sleep=lambda dt: clock.advance(dt))
    assert d.next_batch(timeout=0.05) is None


def test_single_event_returned_after_window_elapses():
    q: queue.Queue[ChangeEvent] = queue.Queue()
    clock = FakeClock()
    d = Debouncer(q, window_ms=300, clock=clock.now, sleep=lambda dt: clock.advance(dt))
    q.put(ChangeEvent(kind="modified", path=Path("a.py")))
    batch = d.next_batch(timeout=1.0)
    assert batch is not None
    assert batch.modified == {Path("a.py")}


def test_burst_of_events_coalesces_into_one_batch():
    q: queue.Queue[ChangeEvent] = queue.Queue()
    clock = FakeClock()
    d = Debouncer(q, window_ms=300, clock=clock.now, sleep=lambda dt: clock.advance(dt))
    q.put(ChangeEvent(kind="modified", path=Path("a.py")))
    q.put(ChangeEvent(kind="modified", path=Path("b.py")))
    q.put(ChangeEvent(kind="deleted", path=Path("c.py")))
    batch = d.next_batch(timeout=1.0)
    assert batch is not None
    assert batch.modified == {Path("a.py"), Path("b.py")}
    assert batch.deleted == {Path("c.py")}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch_debouncer.py -v`
Expected: collection error (`livegraph.watch.debouncer` not found).

- [ ] **Step 3: Implement the Debouncer**

Create `livegraph/watch/debouncer.py`:

```python
from __future__ import annotations

import queue
import time
from collections.abc import Callable

from livegraph.watch.events import ChangeBatch, ChangeEvent


class Debouncer:
    """Coalesce a burst of ChangeEvents into a single ChangeBatch.

    `next_batch(timeout)` blocks until the queue has been quiet for
    `window_ms` after the first event in a burst, or returns None if no
    event arrived within `timeout` seconds.
    """

    def __init__(
        self,
        events: queue.Queue[ChangeEvent],
        window_ms: int = 300,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._events = events
        self._window = window_ms / 1000.0
        self._clock = clock
        self._sleep = sleep

    def next_batch(self, timeout: float = 1.0) -> ChangeBatch | None:
        try:
            first = self._events.get(timeout=timeout)
        except queue.Empty:
            return None

        batch = ChangeBatch.empty().merge(first)
        deadline = self._clock() + self._window
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            try:
                ev = self._events.get(timeout=remaining)
            except queue.Empty:
                break
            batch = batch.merge(ev)
            deadline = self._clock() + self._window
        return batch if not batch.is_empty() else ChangeBatch.empty()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_watch_debouncer.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add livegraph/watch/debouncer.py tests/unit/test_watch_debouncer.py
git commit -m "feat(phase8): Debouncer with injected clock"
```

---

## Task 4: Path filter

**Files:**
- Create: `livegraph/watch/filters.py`
- Test: `tests/unit/test_watch_filters.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_watch_filters.py`:

```python
from __future__ import annotations

from pathlib import Path

from livegraph.watch.filters import PathFilter


def make_filter(tmp_path: Path, **kw) -> PathFilter:
    return PathFilter(root=tmp_path, **kw)


def test_non_python_files_excluded(tmp_path):
    pf = make_filter(tmp_path)
    assert pf.accepts(tmp_path / "a.txt") is False
    assert pf.accepts(tmp_path / "a.py") is True


def test_builtin_ignores(tmp_path):
    pf = make_filter(tmp_path)
    assert pf.accepts(tmp_path / ".git" / "HEAD.py") is False
    assert pf.accepts(tmp_path / "__pycache__" / "x.py") is False
    assert pf.accepts(tmp_path / ".venv" / "lib" / "a.py") is False
    assert pf.accepts(tmp_path / "venv" / "lib" / "a.py") is False
    assert pf.accepts(tmp_path / "node_modules" / "a.py") is False


def test_user_ignore_globs(tmp_path):
    pf = make_filter(tmp_path, user_ignores=["build/*", "*_pb2.py"])
    assert pf.accepts(tmp_path / "build" / "x.py") is False
    assert pf.accepts(tmp_path / "pkg" / "foo_pb2.py") is False
    assert pf.accepts(tmp_path / "pkg" / "foo.py") is True


def test_gitignore_respected(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored/\n*.gen.py\n")
    pf = make_filter(tmp_path)
    assert pf.accepts(tmp_path / "ignored" / "x.py") is False
    assert pf.accepts(tmp_path / "a.gen.py") is False
    assert pf.accepts(tmp_path / "a.py") is True


def test_paths_outside_root_rejected(tmp_path):
    pf = make_filter(tmp_path)
    assert pf.accepts(Path("/etc/passwd.py")) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch_filters.py -v`
Expected: collection error.

- [ ] **Step 3: Implement the filter**

Create `livegraph/watch/filters.py`:

```python
from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from pathlib import Path

_BUILTIN_IGNORES = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


class PathFilter:
    """Decides whether a path should be watched.

    Filters out:
      * non-`.py` files
      * paths outside `root`
      * any path that has a builtin-ignore segment
      * gitignore-style patterns from `root/.gitignore` (if present)
      * user-supplied globs
    """

    def __init__(
        self,
        root: Path,
        *,
        user_ignores: Iterable[str] = (),
    ) -> None:
        self._root = root.resolve()
        self._user_ignores = tuple(user_ignores)
        self._gitignore = _load_gitignore(self._root)

    def accepts(self, path: Path) -> bool:
        try:
            rel = path.resolve().relative_to(self._root)
        except ValueError:
            return False
        if path.suffix != ".py":
            return False
        parts = rel.parts
        if any(seg in _BUILTIN_IGNORES for seg in parts):
            return False
        rel_str = str(rel)
        for pat in self._user_ignores:
            if fnmatch.fnmatch(rel_str, pat):
                return False
            if any(fnmatch.fnmatch(seg, pat.rstrip("/")) for seg in parts):
                return False
        for pat in self._gitignore:
            if _gitignore_match(pat, rel_str, parts):
                return False
        return True


def _load_gitignore(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    out: list[str] = []
    for line in gi.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def _gitignore_match(pat: str, rel_str: str, parts: tuple[str, ...]) -> bool:
    # Directory pattern: matches if any path segment equals the dir name.
    if pat.endswith("/"):
        return pat.rstrip("/") in parts
    if fnmatch.fnmatch(rel_str, pat):
        return True
    return any(fnmatch.fnmatch(seg, pat) for seg in parts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_watch_filters.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add livegraph/watch/filters.py tests/unit/test_watch_filters.py
git commit -m "feat(phase8): PathFilter with gitignore + builtin + user ignores"
```

---

## Task 5: `update_files` — explicit-path entry point

**Files:**
- Modify: `livegraph/incremental.py`
- Test: `tests/unit/test_incremental_update_files.py`

Context: `reingest_files` takes a `ChangeSet`, which is produced by walking the entire project. For watch we already know which paths changed; we don't want to scan the whole tree on every save. We add a thin entry point `update_files` that turns a path list into a `ChangeSet` by hashing only those files and consulting the graph for their stored hashes, then delegates to `reingest_files`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_incremental_update_files.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from livegraph.incremental import update_files


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture()
def project_root(tmp_path):
    write(tmp_path / "pkg" / "__init__.py", "")
    write(tmp_path / "pkg" / "a.py", "def foo():\n    return 1\n")
    write(tmp_path / "pkg" / "b.py", "def bar():\n    return 2\n")
    return tmp_path


def test_no_paths_is_noop(project_root, ingested_backend):
    backend, project = ingested_backend(project_root)
    summary = update_files(str(project_root), backend, project, paths=[])
    assert summary.added == 0
    assert summary.changed == 0
    assert summary.deleted == 0


def test_modified_file_re_ingested(project_root, ingested_backend):
    backend, project = ingested_backend(project_root)
    target = project_root / "pkg" / "a.py"
    target.write_text("def foo():\n    return 99\n")
    summary = update_files(str(project_root), backend, project, paths=[str(target)])
    assert summary.changed == 1
    assert summary.added == 0


def test_deleted_file_purged(project_root, ingested_backend):
    backend, project = ingested_backend(project_root)
    target = project_root / "pkg" / "b.py"
    target.unlink()
    summary = update_files(str(project_root), backend, project, paths=[str(target)])
    assert summary.deleted == 1


def test_created_file_added(project_root, ingested_backend):
    backend, project = ingested_backend(project_root)
    new = project_root / "pkg" / "c.py"
    new.write_text("def baz():\n    return 3\n")
    summary = update_files(str(project_root), backend, project, paths=[str(new)])
    assert summary.added == 1


def test_runtime_calls_preserved_for_untouched_files(project_root, ingested_backend):
    """Editing a.py must not delete runtime CALLS attached to b.py."""
    backend, project = ingested_backend(project_root)
    backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->"
        "(:File {path: $file})-[:DEFINES]->(s:Function {name: 'bar'}) "
        "CREATE (s)-[:CALLS {provenance: 'runtime'}]->(s) "
        "RETURN s.qualified_name AS qn",
        project=project, file="pkg/b.py",
    )
    target = project_root / "pkg" / "a.py"
    target.write_text("def foo():\n    return 42\n")
    update_files(str(project_root), backend, project, paths=[str(target)])
    rows = backend.execute(
        "MATCH (a)-[r:CALLS {provenance: 'runtime'}]->(b) "
        "RETURN count(r) AS n",
    )
    assert rows[0]["n"] >= 1
```

The `ingested_backend` fixture should exist in `tests/conftest.py` from prior phases; if not, add it adapting the existing per-phase fixture. (Inspect `tests/conftest.py` first; if missing this exact fixture, write a small helper that calls `livegraph.ingest.ingest_project(root, backend, project)` and yields `(backend, project)`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_incremental_update_files.py -v`
Expected: `ImportError: cannot import name 'update_files' from livegraph.incremental` (and/or fixture errors which the test author resolves first).

- [ ] **Step 3: Implement `update_files`**

In `livegraph/incremental.py`, just below the existing `reingest_files` function, add:

```python
def update_files(
    root: str,
    backend: GraphBackend,
    project: str,
    paths: Iterable[str],
    batch_size: int = 1000,
) -> UpdateSummary:
    """Re-ingest exactly the files in ``paths`` (relative or absolute).

    Unlike :func:`reingest_files` (which is fed by a whole-tree
    :func:`detect_changes` scan), this entry point only hashes the
    paths the caller passes in. Used by the watch loop, where the
    file system watcher already tells us what changed.
    """
    rels: list[str] = []
    for p in paths:
        ap = os.path.abspath(p)
        try:
            rel = os.path.relpath(ap, root)
        except ValueError:
            continue
        if rel.startswith(".."):
            continue
        rels.append(rel)

    if not rels:
        return UpdateSummary(
            added=0, changed=0, deleted=0, unchanged=0, parse_errors=0,
        )

    stored_rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
        "WHERE f.path IN $paths "
        "RETURN f.path AS path, f.content_hash AS hash",
        project=project, paths=rels,
    )
    stored = {row["path"]: row.get("hash") for row in stored_rows}

    hashes: dict[str, str] = {}
    added: list[str] = []
    changed: list[str] = []
    deleted: list[str] = []
    for rel in rels:
        ap = os.path.join(root, rel)
        if not os.path.exists(ap):
            if rel in stored:
                deleted.append(rel)
            continue
        with open(ap, "rb") as handle:
            h = hashlib.sha256(handle.read()).hexdigest()
        hashes[rel] = h
        if rel not in stored:
            added.append(rel)
        elif stored[rel] != h:
            changed.append(rel)

    cs = ChangeSet(
        added=sorted(added),
        changed=sorted(changed),
        deleted=sorted(deleted),
        unchanged=[],
        hashes=hashes,
    )
    return reingest_files(root, backend, project, cs, batch_size=batch_size)
```

`os` and `hashlib` are already imported at the top of `livegraph/incremental.py`; do not add duplicates. `Iterable` does need to be added — put it with the other top-of-file imports: `from collections.abc import Iterable`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_incremental_update_files.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Run the full unit suite to catch regressions**

Run: `uv run pytest tests/unit/ -v`
Expected: all PASS (no Phase 5 regressions).

- [ ] **Step 6: Commit**

```bash
git add livegraph/incremental.py tests/unit/test_incremental_update_files.py
git commit -m "feat(phase8): update_files entry point for explicit-path reingest"
```

---

## Task 6: `Watcher` — watchdog adapter

**Files:**
- Create: `livegraph/watch/watcher.py`
- (No standalone unit tests; covered by the integration test in Task 9. The pure filter logic is already covered in Task 4.)

- [ ] **Step 1: Implement the adapter**

Create `livegraph/watch/watcher.py`:

```python
"""watchdog-based file system watcher.

Translates raw watchdog events into ChangeEvents and pushes them onto a
queue for the Debouncer to consume.
"""
from __future__ import annotations

import logging
import queue
from collections.abc import Iterable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from livegraph.watch.events import ChangeEvent
from livegraph.watch.filters import PathFilter

log = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, q: queue.Queue[ChangeEvent], pf: PathFilter) -> None:
        self._q = q
        self._pf = pf

    def _emit(self, kind: str, raw_path: str) -> None:
        p = Path(raw_path)
        if not self._pf.accepts(p):
            return
        self._q.put(ChangeEvent(kind=kind, path=p))  # type: ignore[arg-type]

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._emit("created", event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._emit("modified", event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._emit("deleted", event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._emit("deleted", event.src_path)
        dest = getattr(event, "dest_path", None)
        if dest:
            self._emit("created", dest)


class Watcher:
    """Owns a watchdog Observer and pushes ChangeEvents onto a queue."""

    def __init__(
        self,
        root: Path,
        events: queue.Queue[ChangeEvent],
        *,
        user_ignores: Iterable[str] = (),
    ) -> None:
        self._root = root
        self._q = events
        self._pf = PathFilter(root=root, user_ignores=user_ignores)
        self._observer = Observer()

    def start(self) -> None:
        handler = _Handler(self._q, self._pf)
        self._observer.schedule(handler, str(self._root), recursive=True)
        self._observer.start()
        log.info("watching %s", self._root)

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=5.0)
```

- [ ] **Step 2: Smoke import**

Run: `uv run python -c "from livegraph.watch.watcher import Watcher; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add livegraph/watch/watcher.py
git commit -m "feat(phase8): Watcher (watchdog adapter)"
```

---

## Task 7: Loop — orchestrator with backoff

**Files:**
- Create: `livegraph/watch/loop.py`
- Test: `tests/unit/test_watch_loop.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_watch_loop.py`:

```python
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from livegraph.watch.events import ChangeBatch
from livegraph.watch.loop import LoopDeps, run_loop


class FakeDebouncer:
    def __init__(self, batches):
        self._batches = list(batches)

    def next_batch(self, timeout):  # noqa: ARG002
        if not self._batches:
            return _Sentinel
        item = self._batches.pop(0)
        return item


_Sentinel = object()


def stop_after(n_calls):
    counter = {"n": 0}
    def predicate():
        counter["n"] += 1
        return counter["n"] > n_calls
    return predicate


def test_loop_calls_update_files_per_batch():
    batch = ChangeBatch(modified=frozenset({Path("a.py")}), deleted=frozenset())
    update_files = MagicMock(return_value=None)
    deps = LoopDeps(
        backend=MagicMock(),
        project="proj",
        root="/tmp/proj",
        debouncer=FakeDebouncer([batch, batch]),
        update_files=update_files,
        embed_project=None,
        provider=None,
        sleep=lambda dt: None,
        should_stop=stop_after(2),
    )
    run_loop(deps)
    assert update_files.call_count == 2


def test_loop_calls_embed_when_provider_set():
    batch = ChangeBatch(modified=frozenset({Path("a.py")}), deleted=frozenset())
    update_files = MagicMock()
    embed_project = MagicMock()
    deps = LoopDeps(
        backend=MagicMock(),
        project="proj",
        root="/tmp/proj",
        debouncer=FakeDebouncer([batch]),
        update_files=update_files,
        embed_project=embed_project,
        provider=MagicMock(),
        sleep=lambda dt: None,
        should_stop=stop_after(1),
    )
    run_loop(deps)
    embed_project.assert_called_once()


def test_loop_skips_embed_on_parse_errors_no_backoff():
    from livegraph.incremental import UpdateSummary
    batch = ChangeBatch(modified=frozenset({Path("a.py")}), deleted=frozenset())
    update_files = MagicMock(return_value=UpdateSummary(0,0,0,0,1))
    embed_project = MagicMock()
    sleeps = []
    deps = LoopDeps(
        backend=MagicMock(),
        project="proj",
        root="/tmp/proj",
        debouncer=FakeDebouncer([batch]),
        update_files=update_files,
        embed_project=embed_project,
        provider=MagicMock(),
        sleep=lambda dt: sleeps.append(dt),
        should_stop=stop_after(1),
    )
    run_loop(deps)
    embed_project.assert_not_called()
    assert sleeps == []  # no backoff for parse errors


def test_loop_backs_off_on_backend_error():
    batch = ChangeBatch(modified=frozenset({Path("a.py")}), deleted=frozenset())

    calls = {"n": 0}
    def flaky_update(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("neo4j down")
        from livegraph.incremental import UpdateSummary
        return UpdateSummary(0,0,0,0,0)

    sleeps = []
    deps = LoopDeps(
        backend=MagicMock(),
        project="proj",
        root="/tmp/proj",
        debouncer=FakeDebouncer([batch, batch]),
        update_files=flaky_update,
        embed_project=None,
        provider=None,
        sleep=lambda dt: sleeps.append(dt),
        should_stop=stop_after(2),
    )
    run_loop(deps)
    assert sleeps and sleeps[0] >= 1.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_watch_loop.py -v`
Expected: collection error.

- [ ] **Step 3: Implement the loop**

Create `livegraph/watch/loop.py`:

```python
"""Watch loop orchestrator.

Pulls ChangeBatches off the debouncer, drives update_files (and
optionally embed_project), with parse-vs-backend error classification
and exponential backoff for backend failures.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from livegraph.incremental import UpdateSummary

log = logging.getLogger(__name__)

_BACKOFF_START = 1.0
_BACKOFF_CAP = 30.0


@dataclass(frozen=True)
class LoopDeps:
    backend: Any
    project: str
    root: str
    debouncer: Any
    update_files: Callable[..., UpdateSummary]
    embed_project: Callable[..., Any] | None
    provider: Any | None
    sleep: Callable[[float], None] = time.sleep
    should_stop: Callable[[], bool] = lambda: False


def run_loop(deps: LoopDeps) -> None:
    backoff = _BACKOFF_START
    while not deps.should_stop():
        batch = deps.debouncer.next_batch(timeout=1.0)
        if batch is None:
            continue
        if hasattr(batch, "is_empty") and batch.is_empty():
            continue

        paths = [deps.root + "/" + str(p) if not str(p).startswith("/") else str(p)
                 for p in (*batch.modified, *batch.deleted)]
        try:
            summary = deps.update_files(
                deps.root, deps.backend, deps.project, paths=paths,
            )
        except ConnectionError as e:
            log.error("backend error: %s; backing off %.1fs", e, backoff)
            deps.sleep(backoff)
            backoff = min(backoff * 2.0, _BACKOFF_CAP)
            continue
        except Exception:
            log.exception("update_files crashed; continuing")
            continue

        backoff = _BACKOFF_START
        if summary is not None and getattr(summary, "parse_errors", 0):
            log.info("parse errors in batch: %d (continuing)",
                     summary.parse_errors)
            continue

        if deps.embed_project is not None and deps.provider is not None:
            try:
                deps.embed_project(deps.backend, deps.project, deps.provider)
            except Exception as e:
                log.warning("embed step failed (continuing): %s", e)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_watch_loop.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add livegraph/watch/loop.py tests/unit/test_watch_loop.py
git commit -m "feat(phase8): watch loop with backoff + parse/backend error split"
```

---

## Task 8: CLI `livegraph watch`

**Files:**
- Modify: `livegraph/cli.py`
- Test: `tests/unit/test_cli_watch.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_cli_watch.py`:

```python
from __future__ import annotations

from typer.testing import CliRunner

from livegraph.cli import app

runner = CliRunner()


def test_watch_help():
    result = runner.invoke(app, ["watch", "--help"])
    assert result.exit_code == 0
    assert "--project" in result.stdout
    assert "--embed" in result.stdout
    assert "--debounce-ms" in result.stdout
    assert "--ignore" in result.stdout


def test_watch_requires_project(monkeypatch, tmp_path):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    result = runner.invoke(app, ["watch", str(tmp_path)])
    assert result.exit_code == 2


def test_watch_embed_without_extra_exits_1(monkeypatch, tmp_path):
    from livegraph import cli as cli_mod

    def fake_make_backend():
        b = type("B", (), {})()
        b.verify = lambda: None
        b.close = lambda: None
        return b

    monkeypatch.setattr(cli_mod, "_make_backend", fake_make_backend)
    monkeypatch.setattr(cli_mod, "_resolve_root_path", lambda *a, **kw: str(tmp_path))

    from livegraph.semantic.provider import EmbeddingExtraMissing
    def boom(_settings):
        raise EmbeddingExtraMissing("missing extra")
    monkeypatch.setattr(cli_mod, "_make_embedding_provider", boom)

    result = runner.invoke(app, ["watch", "--project", "p", "--embed", str(tmp_path)])
    assert result.exit_code == 1
    assert "semantic" in result.stdout.lower() or "extra" in result.stdout.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli_watch.py -v`
Expected: `watch` command not registered, FAIL.

- [ ] **Step 3: Add the `watch` command to `livegraph/cli.py`**

At the top of `livegraph/cli.py`, ensure these imports exist (add what's missing):

```python
import queue
import signal
from pathlib import Path

from livegraph.watch.debouncer import Debouncer
from livegraph.watch.events import ChangeEvent
from livegraph.watch.loop import LoopDeps, run_loop
from livegraph.watch.watcher import Watcher
from livegraph.incremental import update_files
from livegraph.semantic.embed import embed_project
from livegraph.semantic.provider import EmbeddingExtraMissing
```

At the bottom of the file, add the command:

```python
@app.command()
def watch(
    path: str = typer.Argument(
        None,
        help="Project root (defaults to the Project's stored root_path)",
    ),
    project: str = typer.Option(
        None, "--project",
        help="Ingested project to watch (overrides LIVEGRAPH_PROJECT env)",
    ),
    embed: bool = typer.Option(
        False, "--embed",
        help="After each update, re-run embedding for symbols whose source changed. "
             "Requires the [semantic] extra.",
    ),
    debounce_ms: int = typer.Option(
        None, "--debounce-ms",
        help="Coalesce file events within this window (default 300).",
    ),
    ignore: list[str] = typer.Option(
        None, "--ignore",
        help="Glob patterns to ignore (repeatable). Layered on top of "
             ".gitignore + builtin ignores.",
    ),
) -> None:
    """Mirror source-file edits into the graph live (Ctrl-C to stop)."""
    settings = load_settings()
    resolved_project = project or settings.livegraph_project
    if not resolved_project:
        typer.echo(
            "LIVEGRAPH_PROJECT is not set. Pass --project NAME or set "
            "LIVEGRAPH_PROJECT.",
            err=True,
        )
        raise typer.Exit(code=2)

    backend = _make_backend()
    try:
        backend.verify()
    except ConnectionError as exc:
        typer.echo(f"Neo4j unreachable: {exc}", err=True)
        backend.close()
        raise typer.Exit(code=1) from exc

    try:
        resolved_root = path or _resolve_root_path(backend, resolved_project)
        if not resolved_root:
            typer.echo(
                f"Project {resolved_project!r} has no stored root_path. "
                f"Pass PATH or re-run `livegraph build` to populate it.",
                err=True,
            )
            raise typer.Exit(code=2)

        provider = None
        if embed:
            try:
                provider = _make_embedding_provider(settings)
            except EmbeddingExtraMissing as exc:
                typer.echo(
                    f"--embed requires the [semantic] extra: {exc}",
                    err=True,
                )
                raise typer.Exit(code=1) from exc

        window = debounce_ms or settings.livegraph_watch_debounce_ms
        events: queue.Queue[ChangeEvent] = queue.Queue()
        watcher = Watcher(
            root=Path(resolved_root),
            events=events,
            user_ignores=tuple(ignore or ()),
        )
        debouncer = Debouncer(events, window_ms=window)
        stop_flag = {"stop": False}

        def _request_stop(_signum, _frame):
            stop_flag["stop"] = True

        signal.signal(signal.SIGINT, _request_stop)

        watcher.start()
        typer.echo(
            f"Watching {resolved_root} (project={resolved_project}, "
            f"debounce={window}ms, embed={'on' if embed else 'off'})"
        )

        deps = LoopDeps(
            backend=backend,
            project=resolved_project,
            root=resolved_root,
            debouncer=debouncer,
            update_files=update_files,
            embed_project=embed_project if embed else None,
            provider=provider,
            should_stop=lambda: stop_flag["stop"],
        )
        try:
            run_loop(deps)
        finally:
            watcher.stop()
            typer.echo("Watch stopped.")
    finally:
        backend.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli_watch.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add livegraph/cli.py tests/unit/test_cli_watch.py
git commit -m "feat(phase8): livegraph watch CLI command"
```

---

## Task 9: Integration test (real watchdog + real Neo4j)

**Files:**
- Create: `tests/integration/test_watch_integration.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_watch_integration.py`:

```python
"""End-to-end: watchdog + Debouncer + Loop + real Neo4j."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import pytest

from livegraph.incremental import update_files
from livegraph.watch.debouncer import Debouncer
from livegraph.watch.events import ChangeEvent
from livegraph.watch.loop import LoopDeps, run_loop
from livegraph.watch.watcher import Watcher

pytestmark = pytest.mark.integration


def _wait_for(predicate, timeout=5.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_watch_picks_up_new_file(neo4j_backend, tmp_path):
    backend = neo4j_backend
    project = "watch_test"
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "a.py").write_text("def foo():\n    return 1\n")

    from livegraph.ingest import ingest_project
    ingest_project(str(tmp_path), backend, project)

    events: queue.Queue[ChangeEvent] = queue.Queue()
    watcher = Watcher(root=tmp_path, events=events)
    debouncer = Debouncer(events, window_ms=100)
    stop_flag = {"stop": False}

    deps = LoopDeps(
        backend=backend, project=project, root=str(tmp_path),
        debouncer=debouncer, update_files=update_files,
        embed_project=None, provider=None,
        should_stop=lambda: stop_flag["stop"],
    )
    watcher.start()
    t = threading.Thread(target=run_loop, args=(deps,), daemon=True)
    t.start()
    try:
        (tmp_path / "pkg" / "b.py").write_text("def bar():\n    return 2\n")
        ok = _wait_for(lambda: bool(backend.execute(
            "MATCH (:Project {name: $p})-[:CONTAINS]->(f:File {path: 'pkg/b.py'}) "
            "RETURN f LIMIT 1",
            p=project,
        )))
        assert ok, "new file did not appear in graph within timeout"
    finally:
        stop_flag["stop"] = True
        watcher.stop()
        t.join(timeout=5.0)


def test_watch_picks_up_modification_and_deletion(neo4j_backend, tmp_path):
    backend = neo4j_backend
    project = "watch_test_mod"
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    target = tmp_path / "pkg" / "a.py"
    target.write_text("def foo():\n    return 1\n")

    from livegraph.ingest import ingest_project
    ingest_project(str(tmp_path), backend, project)

    events: queue.Queue[ChangeEvent] = queue.Queue()
    watcher = Watcher(root=tmp_path, events=events)
    debouncer = Debouncer(events, window_ms=100)
    stop_flag = {"stop": False}
    deps = LoopDeps(
        backend=backend, project=project, root=str(tmp_path),
        debouncer=debouncer, update_files=update_files,
        embed_project=None, provider=None,
        should_stop=lambda: stop_flag["stop"],
    )
    watcher.start()
    t = threading.Thread(target=run_loop, args=(deps,), daemon=True)
    t.start()
    try:
        target.write_text(
            "def foo():\n    return 99\n\ndef brand_new():\n    return 7\n"
        )
        ok = _wait_for(lambda: bool(backend.execute(
            "MATCH (s:Function {name: 'brand_new'}) RETURN s LIMIT 1",
        )))
        assert ok, "modification not reflected in graph"

        target.unlink()
        ok = _wait_for(lambda: not backend.execute(
            "MATCH (:Project {name: $p})-[:CONTAINS]->(f:File {path: 'pkg/a.py'}) "
            "RETURN f LIMIT 1",
            p=project,
        ))
        assert ok, "deleted file still present in graph"
    finally:
        stop_flag["stop"] = True
        watcher.stop()
        t.join(timeout=5.0)
```

The `neo4j_backend` fixture is the same one used by other integration tests; reuse the existing one from `tests/integration/conftest.py`.

- [ ] **Step 2: Run with Neo4j running**

Run: `uv run pytest tests/integration/test_watch_integration.py -v -m integration`
Expected: 2 PASS within ~10s total.

- [ ] **Step 3: Run the full suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_watch_integration.py
git commit -m "test(phase8): watch integration against real Neo4j"
```

---

## Task 10: README section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Watch mode" section**

In `README.md`, after the existing "Incremental updates" / Phase 5 section, add:

```markdown
### Watch mode (Phase 8)

Mirror source-file edits into the graph live — useful while an MCP client is
connected.

```bash
livegraph watch --project myproj /path/to/repo
```

Flags:

- `--embed` — after each update, re-run embedding for symbols whose source
  changed (requires `pip install 'livegraph[semantic]'`).
- `--debounce-ms 300` — coalesce file events within this window (default 300).
- `--ignore PATTERN` — glob patterns to ignore (repeatable). Layered on top of
  `.gitignore` + builtin ignores (`.git/`, `__pycache__/`, `.venv/`, `venv/`,
  `node_modules/`).

The loop logs each update; Ctrl-C stops it cleanly. Backend errors trigger
exponential backoff (1s → 30s) so the watcher stays alive if Neo4j blips.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(phase8): README section for watch mode"
```

---

## Acceptance gate (manual, before PR)

- [ ] `uv run pytest -v` → all unit + integration tests pass.
- [ ] `uv run ruff check .` → clean.
- [ ] Manual smoke: in a tmux split, run `livegraph watch --project sample` against the sample project; in another split edit `sample/calculator.py` to add a new function; within ~500ms see a log line; query the graph (`livegraph trace ...` or via MCP `find_symbol`) and confirm the new symbol is present.
- [ ] Manual smoke `--embed`: with `[semantic]` installed, repeat the above with `--embed`; confirm a `semantic_search` MCP call returns the new symbol.
