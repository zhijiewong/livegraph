# livegraph Phase 5 — Incremental Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 5 `livegraph update` CLI command that re-ingests only files whose SHA-256 content hash differs from the value stored on their `File` node, using a two-phase orchestrator that reconciles structure first and resolves CALLS/IMPORTS once after.

**Architecture:** Phase 1's `ingest_project` learns to compute SHA-256 hashes for each file and store them on `File` nodes (plus `root_path` on `Project`). A new `livegraph/incremental.py` module provides `detect_changes()` and `reingest_files()` — the orchestrator walks the filesystem, classifies each file as added/changed/deleted/unchanged, then runs Phase A (reconcile structure: delete removed symbols, MERGE new defs, wipe stale outgoing CALLS/IMPORTS, flag `runtime_stale=true`) followed by Phase B (resolve all reingested files' CALLS and IMPORTS against the now-final project state).

**Tech Stack:** Python 3.12+, no new runtime dependencies. Reuses `hashlib.sha256` (stdlib), the existing Phase 1 extractor and resolver, and the established pure-function + thin-shim pattern.

**Reference:** Design spec at `docs/superpowers/specs/2026-05-23-livegraph-phase5-incremental-design.md`.

**Conventions for every task:**
- Run tests from the repo root: `cd /Users/yvon.zhu/Documents/GitHub/livegraph`.
- Unit tests need no Neo4j. Integration tests are `@pytest.mark.integration` and need Neo4j up (`brew services start neo4j`).
- If git complains about identity, use `git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit ...`.
- All work happens on a feature branch (`implement-phase-5-incremental`) created in Task 1.

---

## Task 1: Branch + sanity check

**Files:**
- No new files (branch only).

- [ ] **Step 1: Create the feature branch**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
git checkout main
git pull --ff-only
git checkout -b implement-phase-5-incremental
```

- [ ] **Step 2: Sanity-check the existing suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 107 passed (or whatever the current main total is), no errors.

## Report
Status (DONE / BLOCKED), exact pytest output, current branch.

---

## Task 2: `FileRecord.content_hash` + writer stores hash and `Project.root_path`

**Files:**
- Modify: `livegraph/models.py`
- Modify: `livegraph/graph/writer.py`
- Test: `tests/unit/test_models.py`
- Test: `tests/unit/test_writer.py`

- [ ] **Step 1: Append a failing test to `tests/unit/test_models.py`**

```python
def test_file_record_supports_content_hash():
    from livegraph.models import FileRecord
    f1 = FileRecord(path="a.py", name="a.py")
    assert f1.content_hash is None
    f2 = FileRecord(path="a.py", name="a.py", content_hash="abc123")
    assert f2.content_hash == "abc123"
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv/bin/pytest tests/unit/test_models.py::test_file_record_supports_content_hash -v
```
Expected: FAIL — `TypeError: ... got an unexpected keyword argument 'content_hash'`.

- [ ] **Step 3: Add the field to `livegraph/models.py`**

Find the existing `FileRecord` dataclass in `livegraph/models.py`. Add `content_hash: str | None = None` as the last field. The block ends up as:

```python
@dataclass(frozen=True, slots=True)
class FileRecord:
    """A source file discovered during ingestion."""

    path: str            # project-relative, forward-slash separated
    name: str            # basename
    language: str = "python"
    parse_error: bool = False
    content_hash: str | None = None
```

- [ ] **Step 4: Verify the model test now passes**

```bash
.venv/bin/pytest tests/unit/test_models.py -v 2>&1 | tail -8
```
Expected: 5 passed (4 existing + 1 new).

- [ ] **Step 5: Append failing tests to `tests/unit/test_writer.py`**

```python
def test_write_files_includes_content_hash_in_row():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    from livegraph.models import FileRecord

    backend = FakeBackend()
    writer = GraphWriter(backend, batch_size=100)
    writer.write_files(
        "proj",
        [FileRecord(path="a.py", name="a.py", content_hash="deadbeef")],
        root_path="/tmp/proj",
    )
    query, params = backend.calls[0]
    assert "content_hash" in query
    assert params["rows"][0]["content_hash"] == "deadbeef"
    assert params["root_path"] == "/tmp/proj"
    assert "root_path" in query


def test_write_files_root_path_is_optional():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    from livegraph.models import FileRecord

    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).write_files(
        "proj", [FileRecord(path="a.py", name="a.py")]
    )
    _query, params = backend.calls[0]
    # When root_path is omitted, the parameter is still passed (as None)
    # so the Cypher's coalesce/SET preserves any existing value.
    assert params.get("root_path") is None
```

- [ ] **Step 6: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_writer.py::test_write_files_includes_content_hash_in_row tests/unit/test_writer.py::test_write_files_root_path_is_optional -v
```
Expected: FAIL (KeyError / AssertionError — root_path not in params, content_hash not in query).

- [ ] **Step 7: Update `GraphWriter.write_files` in `livegraph/graph/writer.py`**

Locate the existing `write_files` method. Replace its body with:

```python
    def write_files(self, project: str, files: Iterable[FileRecord],
                    root_path: str | None = None) -> None:
        """MERGE File nodes and CONTAINS edges from the Project node.

        Stores ``content_hash`` on each File and ``root_path`` on the
        Project (when provided). When ``root_path`` is None, the existing
        ``Project.root_path`` is preserved via ``coalesce``.
        """
        for batch in _batched(files, self._batch_size):
            rows = [
                {"path": f.path, "name": f.name,
                 "language": f.language, "parse_error": f.parse_error,
                 "content_hash": f.content_hash}
                for f in batch
            ]
            self._backend.execute(
                "MERGE (p:Project {name: $project}) "
                "SET p.root_path = coalesce($root_path, p.root_path) "
                "WITH p UNWIND $rows AS row "
                "MERGE (f:File {path: row.path}) "
                "SET f.name = row.name, f.language = row.language, "
                "    f.parse_error = row.parse_error, "
                "    f.content_hash = row.content_hash "
                "MERGE (p)-[:CONTAINS]->(f)",
                project=project, root_path=root_path, rows=rows,
            )
```

- [ ] **Step 8: Run tests**

```bash
.venv/bin/pytest tests/unit/test_writer.py -v 2>&1 | tail -10
```
Expected: all existing writer tests still pass plus the 2 new ones.

- [ ] **Step 9: Verify the full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: previous total + 3 (2 new writer tests + 1 model test), all green.

- [ ] **Step 10: Commit**

```bash
git add livegraph/models.py livegraph/graph/writer.py tests/unit/test_models.py tests/unit/test_writer.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: FileRecord.content_hash and Project.root_path written by Phase 1"
```

## Report
Status, pytest outputs (steps 2, 4, 6, 8, 9), commit SHA.

---

## Task 3: Phase 1 `ingest_project` computes SHA-256 hashes and passes `root_path`

**Files:**
- Modify: `livegraph/ingest.py`
- Test: `tests/unit/test_ingest.py`

- [ ] **Step 1: Append a failing test to `tests/unit/test_ingest.py`**

```python
def test_ingest_stores_content_hashes_and_root_path(tmp_path):
    import hashlib
    from livegraph.graph.backend import FakeBackend
    from livegraph.ingest import ingest_project

    src = "def f():\n    return 1\n"
    (tmp_path / "m.py").write_text(src)
    expected_hash = hashlib.sha256(src.encode()).hexdigest()

    backend = FakeBackend()
    ingest_project(str(tmp_path), backend, project_name="demo",
                   batch_size=100)
    # Find the write_files call (it MERGEs the Project + Files).
    write_files_calls = [c for c in backend.calls if "MERGE (p:Project" in c[0]]
    assert write_files_calls, "expected a write_files call"
    _query, params = write_files_calls[0]
    rows = params["rows"]
    by_path = {row["path"]: row for row in rows}
    assert by_path["m.py"]["content_hash"] == expected_hash
    # Root path is the absolute resolved project root.
    import os
    assert params["root_path"] == os.path.abspath(str(tmp_path))
```

- [ ] **Step 2: Run failing test**

```bash
.venv/bin/pytest tests/unit/test_ingest.py::test_ingest_stores_content_hashes_and_root_path -v
```
Expected: FAIL — content_hash is None or root_path is missing.

- [ ] **Step 3: Modify `livegraph/ingest.py`**

Add to the imports near the top:
```python
import hashlib
```

Inside `ingest_project`, in the file-walk loop, change the source-read + FileRecord construction:

**Before:**
```python
    for rel in rel_paths:
        with open(os.path.join(root, rel), "rb") as handle:
            source = handle.read()
        broken = has_errors(parse_source(source))
        file_records.append(FileRecord(
            path=rel, name=os.path.basename(rel), parse_error=broken))
```

**After:**
```python
    for rel in rel_paths:
        with open(os.path.join(root, rel), "rb") as handle:
            source = handle.read()
        content_hash = hashlib.sha256(source).hexdigest()
        broken = has_errors(parse_source(source))
        file_records.append(FileRecord(
            path=rel, name=os.path.basename(rel), parse_error=broken,
            content_hash=content_hash))
```

Also update the `writer.write_files(...)` call near the end of `ingest_project`:

**Before:**
```python
    writer.write_files(project_name, file_records)
```

**After:**
```python
    writer.write_files(project_name, file_records,
                       root_path=os.path.abspath(root))
```

- [ ] **Step 4: Run test**

```bash
.venv/bin/pytest tests/unit/test_ingest.py -v 2>&1 | tail -8
```
Expected: 3 passed (2 existing + 1 new).

- [ ] **Step 5: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: previous total + 1, all green.

- [ ] **Step 6: Commit**

```bash
git add livegraph/ingest.py tests/unit/test_ingest.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: Phase 1 ingest computes SHA-256 content_hash and stores root_path"
```

## Report
Status, pytest outputs (steps 2, 4, 5), commit SHA.

---

## Task 4: Six new `GraphWriter` methods for incremental ops

**Files:**
- Modify: `livegraph/graph/writer.py`
- Test: `tests/unit/test_writer.py`

- [ ] **Step 1: Append failing tests to `tests/unit/test_writer.py`**

```python
def test_delete_symbols_issues_detach_delete_with_qns():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).delete_symbols(
        ["a.py::f", "a.py::g"])
    query, params = backend.calls[0]
    assert "DETACH DELETE" in query and "UNWIND" in query
    assert params["qns"] == ["a.py::f", "a.py::g"]


def test_delete_symbols_no_op_on_empty():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).delete_symbols([])
    assert backend.calls == []


def test_delete_outgoing_calls_for_file_issues_match_delete():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).delete_outgoing_calls_for_file("a.py")
    query, params = backend.calls[0]
    assert "[c:CALLS]" in query and "DELETE c" in query
    assert params["file"] == "a.py"


def test_delete_imports_from_file_issues_match_delete():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).delete_imports_from_file("a.py")
    query, params = backend.calls[0]
    assert "[r:IMPORTS]" in query and "DELETE r" in query
    assert params["file"] == "a.py"


def test_flag_runtime_stale_for_file():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).flag_runtime_stale_for_file(
        project="proj", file="a.py")
    query, params = backend.calls[0]
    assert "SET s.runtime_stale = true" in query
    assert ":Project {name: $project}" in query
    assert params["project"] == "proj"
    assert params["file"] == "a.py"


def test_clear_runtime_stale_for_symbols():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).clear_runtime_stale_for_symbols(
        ["a.py::f", "a.py::g"])
    query, params = backend.calls[0]
    assert "SET s.runtime_stale = false" in query
    assert params["qns"] == ["a.py::f", "a.py::g"]


def test_clear_runtime_stale_no_op_on_empty():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).clear_runtime_stale_for_symbols([])
    assert backend.calls == []


def test_delete_file_completely():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).delete_file(
        project="proj", file="a.py")
    query, params = backend.calls[0]
    assert "DETACH DELETE" in query
    assert "{path: $file}" in query
    assert params["project"] == "proj"
    assert params["file"] == "a.py"
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_writer.py -v 2>&1 | tail -15
```
Expected: 8 new test failures (AttributeError — methods missing).

- [ ] **Step 3: Append the methods to `livegraph/graph/writer.py`**

Inside the `GraphWriter` class, after the existing `write_coverage` method (or after the last method), add:

```python
    def delete_symbols(self, qns: Iterable[str]) -> None:
        """DETACH DELETE every Function/Method/Class with these qualified_names."""
        qns_list = list(qns)
        if not qns_list:
            return
        self._backend.execute(
            "UNWIND $qns AS qn "
            "MATCH (s {qualified_name: qn}) "
            "WHERE s:Function OR s:Method OR s:Class "
            "DETACH DELETE s",
            qns=qns_list,
        )

    def delete_outgoing_calls_for_file(self, file: str) -> None:
        """Delete every CALLS edge originating in a symbol owned by this file."""
        self._backend.execute(
            "MATCH (s {file: $file})-[c:CALLS]->() "
            "WHERE s:Function OR s:Method "
            "DELETE c",
            file=file,
        )

    def delete_imports_from_file(self, file: str) -> None:
        """Delete every IMPORTS edge originating from this file."""
        self._backend.execute(
            "MATCH (src:File {path: $file})-[r:IMPORTS]->() "
            "DELETE r",
            file=file,
        )

    def flag_runtime_stale_for_file(self, project: str, file: str) -> None:
        """Set runtime_stale=true on every Function/Method in this file."""
        self._backend.execute(
            "MATCH (:Project {name: $project})-[:CONTAINS]->"
            "(:File {path: $file})"
            "-[:DEFINES|HAS_METHOD*1..2]->(s) "
            "WHERE s:Function OR s:Method "
            "SET s.runtime_stale = true",
            project=project, file=file,
        )

    def clear_runtime_stale_for_symbols(self, qns: Iterable[str]) -> None:
        """Set runtime_stale=false on every Function/Method in these qns."""
        qns_list = list(qns)
        if not qns_list:
            return
        self._backend.execute(
            "UNWIND $qns AS qn "
            "MATCH (s {qualified_name: qn}) "
            "WHERE s:Function OR s:Method "
            "SET s.runtime_stale = false",
            qns=qns_list,
        )

    def delete_file(self, project: str, file: str) -> None:
        """DETACH DELETE the File and every symbol it owns (transitive)."""
        self._backend.execute(
            "MATCH (:Project {name: $project})-[:CONTAINS]->"
            "(f:File {path: $file}) "
            "OPTIONAL MATCH (f)-[:DEFINES|HAS_METHOD*1..2]->(s) "
            "WHERE s:Function OR s:Method OR s:Class "
            "DETACH DELETE s, f",
            project=project, file=file,
        )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_writer.py -v 2>&1 | tail -20
```
Expected: all writer tests pass (existing + 8 new).

- [ ] **Step 5: Verify the full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: previous total + 8, all green.

- [ ] **Step 6: Commit**

```bash
git add livegraph/graph/writer.py tests/unit/test_writer.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: GraphWriter methods for incremental graph operations"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 5: `detect_changes` (the classifier)

**Files:**
- Create: `livegraph/incremental.py`
- Test: `tests/unit/test_change_detection.py`

- [ ] **Step 1: Write the failing test** at `tests/unit/test_change_detection.py`:

```python
import hashlib

from livegraph.graph.backend import FakeBackend
from livegraph.incremental import detect_changes, ChangeSet


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_detect_changes_all_unchanged(tmp_path):
    src = "x = 1\n"
    (tmp_path / "a.py").write_text(src)
    h = _hash(src)
    backend = FakeBackend(rows=[{"path": "a.py", "hash": h}])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.unchanged == ["a.py"]
    assert cs.changed == []
    assert cs.added == []
    assert cs.deleted == []


def test_detect_changes_one_changed(tmp_path):
    (tmp_path / "a.py").write_text("x = 2\n")
    backend = FakeBackend(rows=[{"path": "a.py", "hash": _hash("x = 1\n")}])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.changed == ["a.py"]
    assert cs.unchanged == []


def test_detect_changes_added(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    backend = FakeBackend(rows=[])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.added == ["a.py"]
    assert cs.changed == []
    assert cs.deleted == []


def test_detect_changes_deleted(tmp_path):
    backend = FakeBackend(rows=[{"path": "gone.py", "hash": "abc"}])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.deleted == ["gone.py"]
    assert cs.added == []


def test_detect_changes_null_stored_hash_is_changed(tmp_path):
    """Pre-Phase-5 graphs have content_hash=None; treat as changed."""
    (tmp_path / "a.py").write_text("x = 1\n")
    backend = FakeBackend(rows=[{"path": "a.py", "hash": None}])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.changed == ["a.py"]


def test_detect_changes_mixed(tmp_path):
    (tmp_path / "stay.py").write_text("a\n")
    (tmp_path / "edit.py").write_text("new content\n")
    (tmp_path / "new.py").write_text("freshly added\n")
    backend = FakeBackend(rows=[
        {"path": "stay.py", "hash": _hash("a\n")},
        {"path": "edit.py", "hash": _hash("old content\n")},
        {"path": "gone.py", "hash": "anything"},
    ])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.unchanged == ["stay.py"]
    assert cs.changed == ["edit.py"]
    assert cs.added == ["new.py"]
    assert cs.deleted == ["gone.py"]
    # The query parameter was the project name.
    _q, params = backend.calls[0]
    assert params["project"] == "p"


def test_change_set_carries_fresh_hashes(tmp_path):
    src = "x = 1\n"
    (tmp_path / "a.py").write_text(src)
    backend = FakeBackend(rows=[])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.hashes == {"a.py": _hash(src)}
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_change_detection.py -v 2>&1 | tail -5
```
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.incremental'`.

- [ ] **Step 3: Create `livegraph/incremental.py`**

```python
"""Incremental graph updates: detect file changes and re-ingest only those.

`detect_changes` walks the filesystem, computes SHA-256 of every .py file,
and compares against the `content_hash` stored on each `File` node.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

from livegraph.discovery import discover_python_files
from livegraph.graph.backend import GraphBackend


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Classification of every project file vs. the stored graph state."""

    added: list[str]              # rel paths on disk but not in graph
    changed: list[str]            # rel paths with disagreeing hashes
    deleted: list[str]            # rel paths in graph but not on disk
    unchanged: list[str]          # rel paths whose hashes match
    hashes: dict[str, str] = field(default_factory=dict)
    # ``hashes`` maps rel_path -> the freshly-computed disk SHA-256;
    # populated for every on-disk file so the writer can persist it.


def detect_changes(root: str, backend: GraphBackend,
                   project: str) -> ChangeSet:
    """Classify every file in ``root`` vs. the graph's stored state."""
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
        "RETURN f.path AS path, f.content_hash AS hash",
        project=project,
    )
    stored = {row["path"]: row.get("hash") for row in rows}

    on_disk: dict[str, str] = {}
    for rel in discover_python_files(root):
        abs_path = os.path.join(root, rel)
        with open(abs_path, "rb") as handle:
            on_disk[rel] = hashlib.sha256(handle.read()).hexdigest()

    stored_set = set(stored)
    disk_set = set(on_disk)
    added = sorted(disk_set - stored_set)
    deleted = sorted(stored_set - disk_set)
    intersect = disk_set & stored_set
    changed = sorted(p for p in intersect if stored.get(p) != on_disk[p])
    unchanged = sorted(p for p in intersect if stored.get(p) == on_disk[p])

    return ChangeSet(
        added=added, changed=changed, deleted=deleted,
        unchanged=unchanged, hashes=on_disk,
    )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_change_detection.py -v 2>&1 | tail -10
```
Expected: 7 passed.

- [ ] **Step 5: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: previous total + 7, all green.

- [ ] **Step 6: Commit**

```bash
git add livegraph/incremental.py tests/unit/test_change_detection.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: detect_changes classifier for incremental updates"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 6: `reingest_files` (two-phase orchestrator)

**Files:**
- Modify: `livegraph/incremental.py`
- Test: `tests/unit/test_incremental.py`

This is the meatiest task — the two-phase orchestrator. Unit tests use a queued backend (similar to Phase 4's `change_impact` tests) to drive the multi-query flow.

- [ ] **Step 1: Write the failing test** at `tests/unit/test_incremental.py`:

```python
from typing import Any

from livegraph.incremental import (
    ChangeSet, UpdateSummary, reingest_files,
)


class _QueuedBackend:
    """Test backend returning a different canned response per execute call."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def verify(self) -> None:
        return None

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        if not self._responses:
            return []
        return self._responses.pop(0)

    def close(self) -> None:
        return None


def test_reingest_empty_changeset_returns_zero_summary(tmp_path):
    backend = _QueuedBackend([])
    cs = ChangeSet(added=[], changed=[], deleted=[], unchanged=[], hashes={})
    summary = reingest_files(str(tmp_path), backend, project="p",
                             changeset=cs, batch_size=100)
    assert summary == UpdateSummary(
        added=0, changed=0, deleted=0, unchanged=0, parse_errors=0,
    )
    assert backend.calls == []


def test_reingest_deletes_for_each_deleted_path(tmp_path):
    # Phase B will issue 2 reads (project_defined, project_modules) at the end,
    # both returning empty rows.
    backend = _QueuedBackend([[], []])
    cs = ChangeSet(added=[], changed=[],
                   deleted=["a.py", "b.py"], unchanged=[], hashes={})
    summary = reingest_files(str(tmp_path), backend, project="p",
                             changeset=cs, batch_size=100)
    assert summary.deleted == 2
    delete_calls = [c for c in backend.calls if "DETACH DELETE" in c[0]]
    files_deleted = {c[1]["file"] for c in delete_calls if "file" in c[1]}
    assert files_deleted == {"a.py", "b.py"}


def test_reingest_changed_file_does_full_reconcile(tmp_path):
    src = "def f():\n    return 1\n"
    (tmp_path / "m.py").write_text(src)

    # Responses for, in order:
    # 1. Query for old qns of "m.py"  (Phase A step c)  -> [{"qn": "m.py::old"}]
    # 2. delete_symbols (issued; no return value used)
    # 3. write_files (no return)
    # 4. write_definitions (no return)
    # 5. delete_outgoing_calls_for_file (no return)
    # 6. delete_imports_from_file (no return)
    # 7. flag_runtime_stale_for_file (no return)
    # 8. Phase B: read project_defined (returns the new symbol)
    # 9. Phase B: read project_modules
    # 10. Phase B: write_calls (no return)
    # 11. Phase B: write_imports (no return)
    # _QueuedBackend returns [] for calls beyond the queue, so populate as needed:
    backend = _QueuedBackend([
        [{"qn": "m.py::old"}],                         # old qns for m.py
        [],                                            # delete_symbols
        [],                                            # write_files
        [],                                            # write_definitions
        [],                                            # delete_outgoing_calls
        [],                                            # delete_imports
        [],                                            # flag_runtime_stale
        [{"qualified_name": "m.py::f"}],               # project_defined
        [],                                            # project_modules
    ])
    cs = ChangeSet(
        added=[], changed=["m.py"], deleted=[], unchanged=[],
        hashes={"m.py": "newhash"},
    )
    summary = reingest_files(str(tmp_path), backend, project="p",
                             changeset=cs, batch_size=100)

    queries = " | ".join(c[0] for c in backend.calls)
    assert summary.changed == 1
    # The old symbol "m.py::old" should be deleted (Phase A).
    assert any("DETACH DELETE" in q and "qn" in p
               and "m.py::old" in p.get("qns", [])
               for q, p in backend.calls)
    # File MERGE happened with the new hash.
    merge_files = [c for c in backend.calls if "MERGE (p:Project" in c[0]]
    assert merge_files
    assert merge_files[0][1]["rows"][0]["content_hash"] == "newhash"
    # Outgoing CALLS for m.py wiped.
    assert any("[c:CALLS]" in q and "DELETE c" in q for q, _ in backend.calls)
    # IMPORTS for m.py wiped.
    assert any("[r:IMPORTS]" in q and "DELETE r" in q for q, _ in backend.calls)
    # runtime_stale flagged.
    assert any("SET s.runtime_stale = true" in q for q, _ in backend.calls)


def test_reingest_added_file_skips_old_qn_query(tmp_path):
    """For added files, there are no old qns to query/delete."""
    (tmp_path / "new.py").write_text("def f():\n    return 1\n")
    backend = _QueuedBackend([
        # No "old qns" query for added files.
        [],                                            # write_files
        [],                                            # write_definitions
        [],                                            # delete_outgoing_calls
        [],                                            # delete_imports
        [],                                            # flag_runtime_stale
        [{"qualified_name": "new.py::f"}],             # project_defined
        [],                                            # project_modules
    ])
    cs = ChangeSet(
        added=["new.py"], changed=[], deleted=[], unchanged=[],
        hashes={"new.py": "h1"},
    )
    summary = reingest_files(str(tmp_path), backend, project="p",
                             changeset=cs, batch_size=100)
    assert summary.added == 1
    # No DETACH DELETE for symbols should have been issued (no old qns exist).
    detach_symbol_calls = [
        c for c in backend.calls
        if "DETACH DELETE s" in c[0] and "OPTIONAL MATCH" not in c[0]
    ]
    assert detach_symbol_calls == []


def test_reingest_parse_error_records_file_but_skips_symbol_writes(tmp_path):
    (tmp_path / "bad.py").write_text("def f(:\n")
    backend = _QueuedBackend([
        [],                                            # old qns query (returns empty for new file)
        [],                                            # write_files (the parse-error File)
        # No write_definitions because we skip on parse error.
        # No delete_outgoing_calls / delete_imports / flag_runtime_stale
        # are issued either, because we never had a successful parse.
        # Phase B still runs project_defined / project_modules reads.
        [],                                            # project_defined
        [],                                            # project_modules
    ])
    cs = ChangeSet(
        added=[], changed=["bad.py"], deleted=[], unchanged=[],
        hashes={"bad.py": "h"},
    )
    summary = reingest_files(str(tmp_path), backend, project="p",
                             changeset=cs, batch_size=100)
    assert summary.parse_errors == 1
    assert summary.changed == 1   # the file is still counted as processed
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_incremental.py -v 2>&1 | tail -8
```
Expected: FAIL — `ImportError: cannot import name 'UpdateSummary' from 'livegraph.incremental'`.

- [ ] **Step 3: Append `UpdateSummary` and `reingest_files` to `livegraph/incremental.py`**

Add these imports to the top of `livegraph/incremental.py`:

```python
import logging
from collections.abc import Iterable

from livegraph.graph.writer import GraphWriter
from livegraph.models import FileRecord
from livegraph.static.extractor import RawCall, extract
from livegraph.static.parser import has_errors, parse_source
from livegraph.static.resolver import resolve_calls, resolve_imports
```

Then append:

```python
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UpdateSummary:
    """Counts produced by a `reingest_files` run."""

    added: int
    changed: int
    deleted: int
    unchanged: int
    parse_errors: int


def _module_name(rel_path: str) -> str:
    """Dotted module name for a project-relative file path."""
    no_ext = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    parts = no_ext.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _read_project_defined(backend: GraphBackend, project: str) -> set[str]:
    """Return every Function/Method/Class qualified_name in the project."""
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
        "-[:DEFINES|HAS_METHOD*1..2]->(s) "
        "WHERE s:Function OR s:Method OR s:Class "
        "RETURN DISTINCT s.qualified_name AS qualified_name",
        project=project,
    )
    return {row["qualified_name"] for row in rows}


def _read_project_modules(backend: GraphBackend,
                          project: str) -> dict[str, str]:
    """Return the {dotted_module_name: file_path} map for the project."""
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
        "RETURN f.path AS path",
        project=project,
    )
    paths = [row["path"] for row in rows]
    return {_module_name(p): p for p in paths}


def _read_old_qns_for_file(backend: GraphBackend, project: str,
                           file: str) -> set[str]:
    """Return every Function/Method/Class qn currently attributed to ``file``."""
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File {path: $file}) "
        "MATCH (f)-[:DEFINES|HAS_METHOD*1..2]->(s) "
        "WHERE s:Function OR s:Method OR s:Class "
        "RETURN DISTINCT s.qualified_name AS qn",
        project=project, file=file,
    )
    return {row["qn"] for row in rows}


def reingest_files(
    root: str, backend: GraphBackend, project: str,
    changeset: ChangeSet, batch_size: int = 1000,
) -> UpdateSummary:
    """Two-phase incremental re-ingest of changed/added/deleted files."""
    if not (changeset.added or changeset.changed or changeset.deleted):
        return UpdateSummary(added=0, changed=0, deleted=0,
                             unchanged=len(changeset.unchanged),
                             parse_errors=0)

    writer = GraphWriter(backend, batch_size=batch_size)

    # ---- Phase A: structural reconcile ---------------------------------

    # Deletions: drop each missing file and its symbols.
    for rel in changeset.deleted:
        writer.delete_file(project=project, file=rel)

    # Stash per-file Phase-B inputs as we go.
    pending_files: list[tuple[str, list, list]] = []
    parse_errors = 0
    for rel in changeset.added + changeset.changed:
        import os as _os
        with open(_os.path.join(root, rel), "rb") as handle:
            source = handle.read()
        new_hash = changeset.hashes.get(rel)

        # Always upsert the FileRecord, even on parse error (with parse_error=true).
        broken = has_errors(parse_source(source))
        if broken:
            parse_errors += 1
            logger.warning("skipping unparseable file: %s", rel)
            writer.write_files(
                project,
                [FileRecord(path=rel, name=_os.path.basename(rel),
                            parse_error=True, content_hash=new_hash)],
            )
            continue

        defs, imports, raw_calls = extract(rel, source)
        new_qns = {d.qualified_name for d in defs}

        # For changed files, prune any symbols that no longer exist.
        if rel in changeset.changed:
            old_qns = _read_old_qns_for_file(backend, project, rel)
            removed = old_qns - new_qns
            if removed:
                writer.delete_symbols(sorted(removed))

        writer.write_files(
            project,
            [FileRecord(path=rel, name=_os.path.basename(rel),
                        parse_error=False, content_hash=new_hash)],
        )
        writer.write_definitions(defs)
        writer.delete_outgoing_calls_for_file(rel)
        writer.delete_imports_from_file(rel)
        writer.flag_runtime_stale_for_file(project=project, file=rel)
        pending_files.append((rel, imports, raw_calls))

    # ---- Phase B: resolve calls and imports against the final state ----

    if pending_files:
        project_defined = _read_project_defined(backend, project)
        project_modules = _read_project_modules(backend, project)

        for _rel, imports, raw_calls in pending_files:
            edges = resolve_calls(raw_calls, project_defined)
            writer.write_calls(edges)

            resolved_imports = resolve_imports(imports, project_modules)
            _write_imports_for_file(backend, resolved_imports, batch_size)

    return UpdateSummary(
        added=len(changeset.added),
        changed=len(changeset.changed),
        deleted=len(changeset.deleted),
        unchanged=len(changeset.unchanged),
        parse_errors=parse_errors,
    )


def _write_imports_for_file(backend: GraphBackend, imports, batch_size: int) -> None:
    """Write IMPORTS edges for a single file's resolved imports.

    Mirrors the Phase 1 _write_imports helper in ``ingest.py`` but is
    scoped to one file's set; that file's prior IMPORTS were already
    deleted earlier in Phase A so this is a clean MERGE.
    """
    files = [i for i in imports if i.target_kind == "file"]
    modules = [i for i in imports if i.target_kind != "file"]
    if files:
        rows = [{"file": i.file, "target": i.target, "raw": i.raw,
                 "line": i.line} for i in files]
        backend.execute(
            "UNWIND $rows AS row "
            "MATCH (src:File {path: row.file}) "
            "MATCH (dst:File {path: row.target}) "
            "MERGE (src)-[r:IMPORTS]->(dst) "
            "SET r.raw = row.raw, r.line = row.line",
            rows=rows,
        )
    if modules:
        rows = [{"file": i.file, "target": i.target, "kind": i.target_kind,
                 "raw": i.raw, "line": i.line} for i in modules]
        backend.execute(
            "UNWIND $rows AS row "
            "MATCH (src:File {path: row.file}) "
            "MERGE (m:Module {name: row.target}) SET m.kind = row.kind "
            "MERGE (src)-[r:IMPORTS]->(m) "
            "SET r.raw = row.raw, r.line = row.line",
            rows=rows,
        )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_incremental.py -v 2>&1 | tail -10
```
Expected: 5 passed.

If a test fails because the orchestrator issues a different number of queries than the queued-backend supplies, inspect `backend.calls` to see the actual sequence and either:
- Adjust the queue length (the queued backend returns `[]` for calls beyond its responses, which is usually fine for writes).
- Verify the production code follows the documented Phase-A → Phase-B sequence.

Do NOT weaken assertions in the tests.

- [ ] **Step 5: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: previous total + 5, all green.

- [ ] **Step 6: Commit**

```bash
git add livegraph/incremental.py tests/unit/test_incremental.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: two-phase reingest_files orchestrator for incremental updates"
```

## Report
Status, pytest outputs, commit SHA, any orchestrator adjustments you needed.

---

## Task 7: `augment_from_observations` clears `runtime_stale` on observed symbols

**Files:**
- Modify: `livegraph/augment.py`
- Test: `tests/unit/test_augment.py`

- [ ] **Step 1: Append failing test to `tests/unit/test_augment.py`**

```python
def test_augment_clears_runtime_stale_for_observed_symbols():
    from livegraph.augment import augment_from_observations
    from livegraph.graph.backend import FakeBackend

    # Definitions returned by _load_definitions inside augment.
    definitions_rows = [
        {"qualified_name": "calc.py::add", "file": "calc.py",
         "start_line": 1, "end_line": 2, "labels": ["Function"]},
    ]
    backend = FakeBackend(rows=definitions_rows)
    observations = {
        "root": "/tmp/proj",
        "runtime_calls": [
            {"caller_qn": "calc.py::total", "callee_qn": "calc.py::add",
             "test_qn": "calc.py::test_total", "observed_count": 1},
        ],
        "tests": [
            {"qualified_name": "calc.py::test_total", "outcome": "passed",
             "duration": 0.01},
        ],
        "coverage": [
            {"test_context": "calc.py::test_total", "file": "calc.py",
             "lines": [1, 2]},
        ],
    }
    augment_from_observations(observations, backend, batch_size=100)

    clear_calls = [
        c for c in backend.calls
        if "SET s.runtime_stale = false" in c[0]
    ]
    assert clear_calls, "expected a clear_runtime_stale_for_symbols call"
    _q, params = clear_calls[0]
    # The union of caller, callee, test, and covered symbols must be cleared.
    assert "calc.py::add" in params["qns"]
    assert "calc.py::total" in params["qns"]
    assert "calc.py::test_total" in params["qns"]
```

- [ ] **Step 2: Run failing test**

```bash
.venv/bin/pytest tests/unit/test_augment.py::test_augment_clears_runtime_stale_for_observed_symbols -v
```
Expected: FAIL — no `SET s.runtime_stale = false` call.

- [ ] **Step 3: Modify `livegraph/augment.py`**

Find the body of `augment_from_observations`. After the existing `writer.write_coverage(coverage_records)` line and before the `logger.info(...)` line, add the clear-stale pass:

```python
    # Phase 5: clear runtime_stale=false on every symbol observed in this
    # trace run. Symbols not observed keep whatever flag they had.
    observed_qns: set[str] = set()
    for rc in observations.get("runtime_calls", []):
        observed_qns.add(rc["caller_qn"])
        observed_qns.add(rc["callee_qn"])
    for t in observations.get("tests", []):
        observed_qns.add(t["qualified_name"])
    for record in coverage_records:
        observed_qns.add(record.symbol_qn)
    writer.clear_runtime_stale_for_symbols(sorted(observed_qns))
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_augment.py -v 2>&1 | tail -8
```
Expected: all augment tests pass (existing + 1 new).

- [ ] **Step 5: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: previous total + 1, all green.

- [ ] **Step 6: Commit**

```bash
git add livegraph/augment.py tests/unit/test_augment.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: augment clears runtime_stale on every observed symbol"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 8: `livegraph update` CLI subcommand

**Files:**
- Modify: `livegraph/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Append failing tests to `tests/unit/test_cli.py`**

```python
def test_update_command_errors_when_project_missing(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    result = runner.invoke(cli.app, ["update"])
    assert result.exit_code != 0
    assert "LIVEGRAPH_PROJECT" in (result.output + (result.stderr or ""))


def test_update_command_dry_run_does_not_call_reingest(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    backend = FakeBackend(rows=[])  # No stored files -> all added.
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "p")

    called: dict = {"reingest": False}

    def fake_reingest(*args, **kwargs):
        called["reingest"] = True

    monkeypatch.setattr("livegraph.cli.reingest_files", fake_reingest)
    result = runner.invoke(cli.app, ["update", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0
    assert called["reingest"] is False
    assert "added" in result.stdout.lower() or "changed" in result.stdout.lower()


def test_update_command_invokes_reingest_with_changeset(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    backend = FakeBackend(rows=[])  # all added
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "p")

    captured: dict = {}

    def fake_reingest(root, backend_arg, project, changeset, batch_size=1000):
        captured["root"] = root
        captured["project"] = project
        captured["changeset"] = changeset
        from livegraph.incremental import UpdateSummary
        return UpdateSummary(added=1, changed=0, deleted=0, unchanged=0,
                             parse_errors=0)

    monkeypatch.setattr("livegraph.cli.reingest_files", fake_reingest)
    result = runner.invoke(cli.app, ["update", str(tmp_path)])
    assert result.exit_code == 0
    assert captured["project"] == "p"
    assert "a.py" in captured["changeset"].added
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_cli.py -v 2>&1 | tail -10
```
Expected: failures — `Error: No such command 'update'`.

- [ ] **Step 3: Modify `livegraph/cli.py`**

Add to the imports section at the top of `livegraph/cli.py`, after the existing imports:

```python
from livegraph.incremental import detect_changes, reingest_files
```

Add this command before the `if __name__ == "__main__":` block:

```python
@app.command()
def update(
    path: str = typer.Argument(
        None,
        help="Project root (defaults to the Project's stored root_path)",
    ),
    project: str = typer.Option(
        None, "--project",
        help="Ingested project to update (overrides LIVEGRAPH_PROJECT env)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Classify changes; do not write to the graph",
    ),
) -> None:
    """Re-ingest only the files that have changed since the last build."""
    settings = load_settings()
    resolved_project = project or settings.livegraph_project
    if not resolved_project:
        typer.echo(
            "LIVEGRAPH_PROJECT is not set. Pass --project NAME or set the "
            "LIVEGRAPH_PROJECT environment variable.",
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

        changeset = detect_changes(resolved_root, backend, resolved_project)
        typer.echo(
            f"Project: {resolved_project} (root: {resolved_root})"
        )
        typer.echo(
            f"Detected: {len(changeset.changed)} changed, "
            f"{len(changeset.added)} added, "
            f"{len(changeset.deleted)} deleted, "
            f"{len(changeset.unchanged)} unchanged."
        )

        if dry_run:
            for rel in changeset.changed:
                typer.echo(f"  changed: {rel}")
            for rel in changeset.added:
                typer.echo(f"  added:   {rel}")
            for rel in changeset.deleted:
                typer.echo(f"  deleted: {rel}")
            typer.echo("Dry-run: no changes written.")
            return

        summary = reingest_files(
            resolved_root, backend, resolved_project,
            changeset, batch_size=settings.livegraph_batch_size,
        )
        typer.echo(
            f"Update complete: {summary.changed} changed, "
            f"{summary.added} added, {summary.deleted} deleted, "
            f"{summary.unchanged} unchanged, "
            f"{summary.parse_errors} parse errors."
        )
    finally:
        backend.close()


def _resolve_root_path(backend, project: str) -> str | None:
    """Look up Project.root_path on the graph, or None if absent."""
    rows = backend.execute(
        "MATCH (p:Project {name: $project}) RETURN p.root_path AS root_path",
        project=project,
    )
    if not rows:
        return None
    return rows[0].get("root_path")
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_cli.py -v 2>&1 | tail -15
```
Expected: all existing CLI tests pass + 3 new tests pass.

- [ ] **Step 5: Verify full unit suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: previous total + 3, all green.

- [ ] **Step 6: Commit**

```bash
git add livegraph/cli.py tests/unit/test_cli.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: livegraph update CLI subcommand"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 9: Real-Neo4j integration tests

**Files:**
- Create: `tests/integration/test_incremental_integration.py`

Reuses Phase 3's `ingested_sample` fixture. Each test copies the sample project to `tmp_path` so we can edit the on-disk fixture without affecting other integration tests.

- [ ] **Step 1: Verify Neo4j is up**

```bash
(echo > /dev/tcp/localhost/7687) 2>/dev/null && echo "neo4j up" || (echo "neo4j DOWN — starting" && brew services start neo4j && for i in $(seq 1 30); do (echo > /dev/tcp/localhost/7687) 2>/dev/null && echo "up after ${i}s" && break; sleep 1; done)
```

- [ ] **Step 2: Write the integration tests** at `tests/integration/test_incremental_integration.py`:

```python
"""End-to-end tests for incremental updates against a real Neo4j."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from livegraph.augment import augment_from_observations
from livegraph.incremental import detect_changes, reingest_files
from livegraph.ingest import ingest_project
from livegraph.runtime.runner import run_pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def mutable_sample(tmp_path, sample_project_path, neo4j_backend):
    """Copy the sample project to a writable tmp dir and ingest it.

    Yields ``(backend, project, root)`` where root is the tmp copy you may
    edit freely without affecting other integration tests' fixtures.
    """
    root = tmp_path / "sample"
    shutil.copytree(sample_project_path, root)
    ingest_project(str(root), neo4j_backend, project_name="sample",
                   batch_size=100)
    observations = run_pytest(str(root), python=sys.executable)
    augment_from_observations(observations, neo4j_backend, batch_size=100)
    yield neo4j_backend, "sample", str(root)


def test_update_no_op_when_nothing_changed(mutable_sample):
    backend, project, root = mutable_sample
    cs = detect_changes(root, backend, project)
    assert cs.changed == [] and cs.added == [] and cs.deleted == []
    assert set(cs.unchanged) == {"calculator.py", "runner.py",
                                 "test_calculator.py"}
    summary = reingest_files(root, backend, project, cs, batch_size=100)
    assert summary == type(summary)(
        added=0, changed=0, deleted=0,
        unchanged=3, parse_errors=0,
    )


def test_update_adds_new_function_to_runner(mutable_sample):
    backend, project, root = mutable_sample
    # Append a new top-level function to runner.py.
    runner_py = Path(root) / "runner.py"
    runner_py.write_text(
        runner_py.read_text()
        + "\n\ndef brand_new():\n    return 42\n"
    )
    cs = detect_changes(root, backend, project)
    assert cs.changed == ["runner.py"]
    reingest_files(root, backend, project, cs, batch_size=100)

    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(:File "
        "    {path: 'runner.py'})-[:DEFINES]->(f:Function "
        "    {qualified_name: 'runner.py::brand_new'}) "
        "RETURN f.name AS name",
        project=project,
    )
    assert rows == [{"name": "brand_new"}]

    # The differentiator edge from Phase 2 must survive the update,
    # because MERGE-by-qualified_name preserves incoming CALLS edges.
    edge = backend.execute(
        "MATCH (:Function {qualified_name: 'runner.py::run_operation'})"
        "-[c:CALLS]->(:Method "
        "    {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN c.runtime AS runtime",
        project=project,
    )
    assert edge == [{"runtime": True}]


def test_update_removes_a_method(mutable_sample):
    backend, project, root = mutable_sample
    # Remove Calculator.multiply.
    calc = Path(root) / "calculator.py"
    new_src = calc.read_text().replace(
        "    def multiply(self, a, b):\n        return a * b\n", "",
    )
    calc.write_text(new_src)

    cs = detect_changes(root, backend, project)
    assert cs.changed == ["calculator.py"]
    reingest_files(root, backend, project, cs, batch_size=100)

    rows = backend.execute(
        "MATCH (m:Method "
        "    {qualified_name: 'calculator.py::Calculator.multiply'}) "
        "RETURN m.name AS name",
    )
    assert rows == []   # the method node is gone


def test_update_handles_deleted_file(mutable_sample):
    backend, project, root = mutable_sample
    (Path(root) / "runner.py").unlink()

    cs = detect_changes(root, backend, project)
    assert cs.deleted == ["runner.py"]
    reingest_files(root, backend, project, cs, batch_size=100)

    files = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File "
        "    {path: 'runner.py'}) RETURN f.path AS path",
        project=project,
    )
    assert files == []

    # The dynamic-dispatch CALLS edge is gone with run_operation.
    edge = backend.execute(
        "MATCH (a {qualified_name: 'runner.py::run_operation'})-[c:CALLS]->"
        "(b {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN c",
    )
    assert edge == []


def test_runtime_stale_lifecycle(mutable_sample):
    backend, project, root = mutable_sample
    # Edit calculator.py.
    calc = Path(root) / "calculator.py"
    calc.write_text(calc.read_text() + "\n# trailing comment\n")
    cs = detect_changes(root, backend, project)
    reingest_files(root, backend, project, cs, batch_size=100)

    rows = backend.execute(
        "MATCH (m:Method "
        "    {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN coalesce(m.runtime_stale, false) AS stale",
    )
    assert rows == [{"stale": True}]

    # Re-trace; runtime_stale should be cleared for the observed symbol.
    observations = run_pytest(root, python=sys.executable)
    augment_from_observations(observations, backend, batch_size=100)

    rows = backend.execute(
        "MATCH (m:Method "
        "    {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN coalesce(m.runtime_stale, false) AS stale",
    )
    assert rows == [{"stale": False}]


def test_dry_run_does_not_modify_graph(mutable_sample):
    backend, project, root = mutable_sample
    calc = Path(root) / "calculator.py"
    orig_text = calc.read_text()
    calc.write_text(orig_text + "\n# touched\n")

    # Capture the stored content_hash before dry-run.
    before = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File "
        "    {path: 'calculator.py'}) RETURN f.content_hash AS h",
        project=project,
    )

    cs = detect_changes(root, backend, project)
    assert cs.changed == ["calculator.py"]
    # In a dry run we do NOT call reingest_files at all — the CLI is the
    # one that decides to skip the call. Here we simulate the dry-run path
    # by simply not calling reingest_files and verifying the graph is
    # unchanged.
    after = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File "
        "    {path: 'calculator.py'}) RETURN f.content_hash AS h",
        project=project,
    )
    assert before == after
```

- [ ] **Step 3: Run the integration tests**

```bash
.venv/bin/pytest tests/integration/test_incremental_integration.py -v -m integration 2>&1 | tail -25
```
Expected: 6 passed.

If a test fails, DO NOT weaken assertions. Common debugging steps:
- Check what `detect_changes` actually returned: `print(cs)` in the test, re-run.
- Check the graph state at each step in Neo4j Browser.
- For `test_runtime_stale_lifecycle`, the comment-only edit doesn't change executed behavior; `Calculator.add` still gets covered by the test suite, so `clear_runtime_stale_for_symbols` should set it back to `false`. If the test sees `stale=True` after re-trace, debug whether the observations payload includes `calculator.py::Calculator.add` in any of `runtime_calls`/`tests`/`coverage`.

- [ ] **Step 4: Run the entire test suite (no regressions)**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
.venv/bin/pytest -m integration -q 2>&1 | tail -3
```
Expected: previous totals + the new tests, all green.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_incremental_integration.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "test: incremental update integration tests against real Neo4j"
```

## Report
Status (DONE / BLOCKED), full Step 3 output, Step 4 totals, commit SHA, any debugging you did.

---

## Task 10: README update + final verify

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the README**

Append a new section to `README.md` after the existing "Quick start" / MCP sections:

```markdown

## Keeping the graph in sync (`livegraph update`)

After the first `livegraph build`, subsequent edits don't require a full rebuild.
Run:

```bash
LIVEGRAPH_PROJECT=myproject livegraph update
```

The command walks the project, computes SHA-256 hashes of every `.py` file,
compares against the hashes stored on `File` nodes, and re-ingests only the
files whose content actually changed. Deletions are removed from the graph;
new files are added; unchanged files are skipped.

Runtime data (from `livegraph trace`) is preserved on changed-file symbols
but flagged `runtime_stale=true`. A subsequent `livegraph trace` clears the
flag on every symbol that appears in the new observations.

Use `--dry-run` to preview the classification without writing to the graph:

```bash
livegraph update --dry-run
```

Known limitation: a function renamed in file A while file B still calls it
by the old name leaves an orphaned `CALLS` edge until file B is also touched.
Run a full `livegraph build` to fully recover.
```

- [ ] **Step 2: Final full-suite verify**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
.venv/bin/pytest -m integration -q 2>&1 | tail -3
.venv/bin/ruff check livegraph 2>&1 | tail -5
```
Expected: all unit + all integration tests pass; ruff clean. Fix any ruff issues that came from Phase 5 code (only files modified in this branch: `livegraph/incremental.py`, `livegraph/cli.py`, `livegraph/ingest.py`, `livegraph/augment.py`, `livegraph/models.py`, `livegraph/graph/writer.py`).

- [ ] **Step 3: Commit**

```bash
git add README.md
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "docs: add livegraph update section to README"
```

## Report
Status, exact pytest unit total, exact pytest integration total, ruff result, commit SHA.

---

## Done

After Task 10, `livegraph update` re-ingests only the files whose SHA-256 content hash differs from the value stored on their `File` node. The graph stays in sync with the codebase between full builds, without a daemon, without race conditions, without surprises.

Try it manually after merging:

```bash
# After an initial build:
livegraph build /path/to/python/project

# Edit some files, then:
LIVEGRAPH_PROJECT=<name> livegraph update

# Or to preview:
LIVEGRAPH_PROJECT=<name> livegraph update --dry-run
```

`graph_status` (via MCP or Neo4j Browser) will show that `runtime_stale=true`
is set on symbols whose file was edited but not yet re-traced. A subsequent
`livegraph trace` clears the flag on every observed symbol.

Out of scope (deliberately deferred): `livegraph watch` daemon, an MCP tool
that triggers update from the agent's side, auto-running `livegraph trace`
after `update`, diff-aware staleness, exposing `runtime_stale` in every MCP
tool's return shape, multi-language. Each remains a candidate for a future
spec.
