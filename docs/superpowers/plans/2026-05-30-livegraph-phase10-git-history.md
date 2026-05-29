# livegraph Phase 10 — Git-history layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a time/authorship axis to the graph: ingest a project's git history into `(:Commit)` / `(:Author)` nodes with `CHANGED_IN` edges (file-level fallback + symbol-level via line overlap), expose three new MCP tools (`symbol_history`, `recent_changes`, `top_churn`).

**Architecture:** A new `livegraph/history/` package mirrors the Phase 1 ingest layout: `extractor.py` shells out to `git log` + `git diff-tree` and yields commit records; `attributor.py` maps hunk line ranges to current-source symbols via a single backend query per file; `writer.py` batches Cypher writes; `ingest.py` orchestrates. CLI gains `livegraph ingest-history`. Three new MCP tools live in `livegraph/mcp/tools_history.py`.

**Tech Stack:** Python 3.12+, subprocess + git CLI (no Python git library), existing Neo4j backend, existing FastMCP, existing Typer CLI.

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `livegraph/history/__init__.py` | Create | Package marker. |
| `livegraph/history/models.py` | Create | `CommitRecord`, `FileChange`, `HunkRange` dataclasses. Pure data. |
| `livegraph/history/extractor.py` | Create | `iter_commits(root, since=None, max_commits=None)` — subprocess wrapper around git. |
| `livegraph/history/attributor.py` | Create | `attribute_hunks(backend, project, file_path, hunks)` — overlap query. |
| `livegraph/history/writer.py` | Create | `HistoryWriter` — UNWIND-batched writes for Commit/Author/CHANGED_IN/AUTHORED. |
| `livegraph/history/ingest.py` | Create | `ingest_history(root, backend, project, since_last=False, max_commits=None)` orchestrator. |
| `livegraph/cli.py` | Modify | New `ingest-history` subcommand. |
| `livegraph/mcp/tools_history.py` | Create | `symbol_history`, `recent_changes`, `top_churn` + their Cypher. |
| `livegraph/mcp/server.py` | Modify | Register the 3 new tools (16-18). "15 tools" → "18 tools". |
| `tests/integration/test_mcp_server_smoke.py` | Modify | Add 3 names to the expected list. |
| `tests/unit/test_history_extractor.py` | Create | Real tempdir git repo; verifies commits + hunks + .mailmap. |
| `tests/unit/test_history_attributor.py` | Create | Fake backend; overlap logic. |
| `tests/unit/test_history_writer.py` | Create | Canned backend; verifies UNWIND batches + idempotence. |
| `tests/unit/test_history_ingest.py` | Create | Mocked extractor/attributor/writer; orchestration + `--since-last` SHA. |
| `tests/unit/test_mcp_tools_history.py` | Create | 3 tools × happy path + warning case. |
| `tests/unit/test_cli_ingest_history.py` | Create | Flag parsing, not-a-git-repo, missing project. |
| `tests/integration/test_history_integration.py` | Create | Real git + real Neo4j; assert tool results. |
| `README.md` | Modify | Add "Git history" section. |

---

## Task 1: Data models

**Files:**
- Create: `livegraph/history/__init__.py`
- Create: `livegraph/history/models.py`

- [ ] **Step 1: Create the package and `models.py`**

`livegraph/history/__init__.py`:

```python
from livegraph.history.models import CommitRecord, FileChange, HunkRange

__all__ = ["CommitRecord", "FileChange", "HunkRange"]
```

`livegraph/history/models.py`:

```python
"""Data classes for the git-history layer."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class HunkRange:
    """A single post-image hunk range from `git diff-tree`.

    `start` and `end` are inclusive 1-based line numbers in the file as
    it exists at the commit (the "after" side of the diff).
    """
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class FileChange:
    """One file's changes in a single commit."""
    path: str
    additions: int
    deletions: int
    hunks: tuple[HunkRange, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CommitRecord:
    """One commit with all its file changes."""
    sha: str
    short_sha: str
    message: str
    timestamp: str        # ISO-8601, sortable
    author_email: str
    author_name: str
    files: tuple[FileChange, ...] = field(default_factory=tuple)
```

- [ ] **Step 2: Smoke-import**

```
.venv/bin/python -c "from livegraph.history import CommitRecord, FileChange, HunkRange; print('ok')"
```
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add livegraph/history/__init__.py livegraph/history/models.py
git commit -m "feat(phase10): history models — CommitRecord, FileChange, HunkRange"
```

---

## Task 2: Extractor

**Files:**
- Create: `livegraph/history/extractor.py`
- Test: `tests/unit/test_history_extractor.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_history_extractor.py`:

```python
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from livegraph.history.extractor import iter_commits


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    base_env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00",
                "GIT_COMMITTER_DATE": "2026-01-01T00:00:00",
                "EMAIL": "default@example.com",
                "GIT_AUTHOR_NAME": "Default", "GIT_AUTHOR_EMAIL": "default@example.com",
                "GIT_COMMITTER_NAME": "Default",
                "GIT_COMMITTER_EMAIL": "default@example.com"}
    if env:
        base_env.update(env)
    subprocess.run(["git", *args], cwd=repo, env=base_env, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.fixture()
def tiny_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "alice@example.com")
    _git(tmp_path, "config", "user.name", "Alice")
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "add foo",
         env={"GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@example.com",
              "GIT_COMMITTER_NAME": "Alice",
              "GIT_COMMITTER_EMAIL": "alice@example.com"})
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n")
    (tmp_path / "b.py").write_text("def bar():\n    return 1\n")
    _git(tmp_path, "add", "a.py", "b.py")
    _git(tmp_path, "commit", "-q", "-m", "tweak foo + add bar",
         env={"GIT_AUTHOR_NAME": "Bob", "GIT_AUTHOR_EMAIL": "bob@example.com",
              "GIT_COMMITTER_NAME": "Bob",
              "GIT_COMMITTER_EMAIL": "bob@example.com"})
    return tmp_path


def test_iter_commits_returns_commits_newest_first(tiny_repo):
    commits = list(iter_commits(str(tiny_repo)))
    assert len(commits) == 2
    assert commits[0].message == "tweak foo + add bar"
    assert commits[1].message == "add foo"


def test_commit_carries_author_email_and_name(tiny_repo):
    commits = list(iter_commits(str(tiny_repo)))
    assert commits[0].author_email == "bob@example.com"
    assert commits[0].author_name == "Bob"
    assert commits[1].author_email == "alice@example.com"


def test_commit_carries_file_changes_with_hunks(tiny_repo):
    commits = list(iter_commits(str(tiny_repo)))
    head = commits[0]
    paths = {f.path for f in head.files}
    assert paths == {"a.py", "b.py"}
    a = next(f for f in head.files if f.path == "a.py")
    # Modifying line 2 of a 2-line file: hunk should cover at least line 2.
    assert a.hunks and any(h.start <= 2 <= h.end for h in a.hunks)
    assert a.additions >= 1 and a.deletions >= 1


def test_since_argument_walks_only_newer_commits(tiny_repo):
    all_commits = list(iter_commits(str(tiny_repo)))
    older_sha = all_commits[-1].sha
    newer_only = list(iter_commits(str(tiny_repo), since=older_sha))
    assert [c.message for c in newer_only] == ["tweak foo + add bar"]


def test_max_commits_caps_the_walk(tiny_repo):
    commits = list(iter_commits(str(tiny_repo), max_commits=1))
    assert len(commits) == 1
    assert commits[0].message == "tweak foo + add bar"
```

- [ ] **Step 2: Run them — expect collection error**

```
.venv/bin/python -m pytest tests/unit/test_history_extractor.py -v
```
Expected: collection error (no `iter_commits`).

- [ ] **Step 3: Implement the extractor**

Create `livegraph/history/extractor.py`:

```python
"""Walk a project's git history via subprocess and yield CommitRecords.

We shell out to `git log` and `git diff-tree` rather than depending on a
Python git library. This keeps livegraph's runtime deps minimal and
exactly matches what the user sees from `git` on their command line.
"""
from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

from livegraph.history.models import CommitRecord, FileChange, HunkRange

log = logging.getLogger(__name__)

_LOG_FORMAT = "%H%x1f%h%x1f%aI%x1f%aE%x1f%aN%x1f%s"
_HUNK_HEADER = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _run_git(root: str, args: list[str]) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _split_log_record(raw: bytes) -> tuple[str, str, str, str, str, str]:
    parts = raw.split(b"\x1f", 5)
    if len(parts) != 6:
        raise ValueError(f"malformed git log record: {raw!r}")
    return tuple(p.decode("utf-8", errors="replace") for p in parts)  # type: ignore[return-value]


def _parse_hunks(diff_bytes: bytes) -> list[HunkRange]:
    hunks: list[HunkRange] = []
    for line in diff_bytes.splitlines():
        m = _HUNK_HEADER.match(line)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) else 1
        if count == 0:
            # Pure deletion — no post-image lines. Skip; numstat already
            # records the deletion count.
            continue
        hunks.append(HunkRange(start=start, end=start + count - 1))
    return hunks


def _diff_tree_for(root: str, sha: str, path: str) -> bytes:
    try:
        return _run_git(
            root,
            ["diff-tree", "-p", "--no-renames", "--no-color",
             "--unified=0", sha, "--", path],
        )
    except subprocess.CalledProcessError as exc:
        log.warning("diff-tree failed for %s %s: %s", sha[:8], path,
                    exc.stderr.decode("utf-8", errors="replace"))
        return b""


def _numstat_for(root: str, sha: str) -> list[tuple[int, int, str]]:
    out = _run_git(
        root,
        ["log", "-1", "--first-parent", "--no-merges", "--numstat",
         "--format=", sha],
    )
    rows: list[tuple[int, int, str]] = []
    for line in out.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        adds_s, dels_s, path = parts
        adds = 0 if adds_s == "-" else int(adds_s)
        dels = 0 if dels_s == "-" else int(dels_s)
        rows.append((adds, dels, path))
    return rows


def iter_commits(
    root: str,
    since: str | None = None,
    max_commits: int | None = None,
) -> Iterator[CommitRecord]:
    """Walk first-parent non-merge commits, newest first.

    ``since`` exclusive — pass a SHA and we walk from HEAD back to (but
    not including) that SHA.
    """
    git_dir = Path(root) / ".git"
    if not git_dir.exists():
        raise ValueError(f"not a git repository: {root}")

    log_args = ["log", "--first-parent", "--no-merges",
                f"--pretty=format:{_LOG_FORMAT}"]
    if since:
        log_args.append(f"{since}..HEAD")
    if max_commits is not None:
        log_args.extend(["-n", str(max_commits)])

    log_bytes = _run_git(root, log_args)
    if not log_bytes.strip():
        return
    records = log_bytes.split(b"\n")
    for raw in records:
        if not raw.strip():
            continue
        try:
            sha, short_sha, ts, email, name, message = _split_log_record(raw)
        except ValueError as exc:
            log.warning("skipping unparseable commit: %s", exc)
            continue

        numstat = _numstat_for(root, sha)
        if not numstat:
            continue

        files: list[FileChange] = []
        for adds, dels, path in numstat:
            hunks = tuple(_parse_hunks(_diff_tree_for(root, sha, path)))
            files.append(FileChange(
                path=path, additions=adds, deletions=dels, hunks=hunks,
            ))

        yield CommitRecord(
            sha=sha, short_sha=short_sha, message=message,
            timestamp=ts, author_email=email, author_name=name,
            files=tuple(files),
        )
```

- [ ] **Step 4: Run tests — expect all PASS**

```
.venv/bin/python -m pytest tests/unit/test_history_extractor.py -v
```
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add livegraph/history/extractor.py tests/unit/test_history_extractor.py
git commit -m "feat(phase10): history extractor — iter_commits with hunks"
```

---

## Task 3: Attributor

**Files:**
- Create: `livegraph/history/attributor.py`
- Test: `tests/unit/test_history_attributor.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_history_attributor.py`:

```python
from __future__ import annotations

from typing import Any

from livegraph.history.attributor import attribute_hunks
from livegraph.history.models import HunkRange


class _FakeBackend:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        return self._rows

    def verify(self): return None
    def close(self): return None


def test_no_hunks_returns_empty_dict():
    backend = _FakeBackend([])
    out = attribute_hunks(backend, project="p", file_path="a.py", hunks=())
    assert out == {}


def test_no_overlapping_symbols_returns_empty_dict():
    # File has one symbol on lines 1-5; hunk touches line 20.
    backend = _FakeBackend([
        {"qualified_name": "pkg.foo", "start_line": 1, "end_line": 5},
    ])
    out = attribute_hunks(
        backend, project="p", file_path="a.py",
        hunks=(HunkRange(start=20, end=22),),
    )
    assert out == {}


def test_single_overlap_returns_lines_count():
    backend = _FakeBackend([
        {"qualified_name": "pkg.foo", "start_line": 1, "end_line": 10},
    ])
    out = attribute_hunks(
        backend, project="p", file_path="a.py",
        hunks=(HunkRange(start=5, end=7),),
    )
    # Lines 5,6,7 all inside [1..10]: 3 lines.
    assert out == {"pkg.foo": 3}


def test_multiple_hunks_accumulate_per_symbol():
    backend = _FakeBackend([
        {"qualified_name": "pkg.foo", "start_line": 1, "end_line": 10},
        {"qualified_name": "pkg.bar", "start_line": 11, "end_line": 20},
    ])
    out = attribute_hunks(
        backend, project="p", file_path="a.py",
        hunks=(
            HunkRange(start=2, end=4),    # foo: 3 lines
            HunkRange(start=8, end=12),   # foo: 8..10 (3) + bar: 11..12 (2)
            HunkRange(start=18, end=18),  # bar: 1 line
        ),
    )
    assert out == {"pkg.foo": 6, "pkg.bar": 3}


def test_query_scopes_to_project_and_file():
    backend = _FakeBackend([])
    attribute_hunks(
        backend, project="myproj", file_path="pkg/a.py",
        hunks=(HunkRange(start=1, end=1),),
    )
    assert backend.calls, "expected at least one backend.execute call"
    cypher, params = backend.calls[0]
    assert params["project"] == "myproj"
    assert params["file"] == "pkg/a.py"
```

- [ ] **Step 2: Run them — expect collection error**

- [ ] **Step 3: Implement the attributor**

Create `livegraph/history/attributor.py`:

```python
"""Attribute commit hunks to current-source symbols by line overlap.

We look up symbols defined in the file in question, then compute the
overlap between each hunk and each symbol's `start_line..end_line`. The
attribution uses the CURRENT parse, so a symbol whose lines moved
across history may be over- or under-credited for past commits. That's
the documented trade-off; see the design spec.
"""
from __future__ import annotations

from collections.abc import Iterable

from livegraph.graph.backend import GraphBackend
from livegraph.history.models import HunkRange

_FILE_SYMBOLS_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->"
    "(:File {path: $file})-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE s:Function OR s:Method "
    "RETURN s.qualified_name AS qualified_name, "
    "       s.start_line AS start_line, "
    "       s.end_line AS end_line"
)


def _overlap_lines(hunk: HunkRange, sym_start: int, sym_end: int) -> int:
    lo = max(hunk.start, sym_start)
    hi = min(hunk.end, sym_end)
    return max(0, hi - lo + 1)


def attribute_hunks(
    backend: GraphBackend,
    project: str,
    file_path: str,
    hunks: Iterable[HunkRange],
) -> dict[str, int]:
    """Return {qualified_name: total_overlapped_lines} for the file's
    symbols against the given hunks. Returns {} if no hunks or no
    overlap.
    """
    hunks = tuple(hunks)
    if not hunks:
        return {}
    rows = backend.execute(
        _FILE_SYMBOLS_CYPHER, project=project, file=file_path,
    )
    if not rows:
        return {}

    out: dict[str, int] = {}
    for row in rows:
        qn = row.get("qualified_name")
        s_start = row.get("start_line")
        s_end = row.get("end_line")
        if qn is None or s_start is None or s_end is None:
            continue
        total = 0
        for h in hunks:
            total += _overlap_lines(h, int(s_start), int(s_end))
        if total > 0:
            out[qn] = out.get(qn, 0) + total
    return out
```

- [ ] **Step 4: Run tests — expect 5 PASS**

- [ ] **Step 5: Commit**

```bash
git add livegraph/history/attributor.py tests/unit/test_history_attributor.py
git commit -m "feat(phase10): history attributor — hunk→symbol line overlap"
```

---

## Task 4: Writer

**Files:**
- Create: `livegraph/history/writer.py`
- Test: `tests/unit/test_history_writer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_history_writer.py`:

```python
from __future__ import annotations

from typing import Any

from livegraph.history.models import CommitRecord, FileChange
from livegraph.history.writer import HistoryWriter


class _RecordingBackend:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        return []

    def verify(self): return None
    def close(self): return None


def _commit(sha: str = "a" * 40, files=()) -> CommitRecord:
    return CommitRecord(
        sha=sha, short_sha=sha[:7], message="m",
        timestamp="2026-01-01T00:00:00+00:00",
        author_email="alice@x", author_name="Alice", files=tuple(files),
    )


def test_writer_writes_project_commit_and_author():
    backend = _RecordingBackend()
    w = HistoryWriter(backend, batch_size=10)
    w.write_commits(project="p", commits=[
        _commit(files=(FileChange(path="a.py", additions=1, deletions=0),)),
    ])
    cyphers = " || ".join(c[0] for c in backend.calls)
    assert "MERGE (a:Author" in cyphers
    assert "MERGE (c:Commit" in cyphers
    assert "MERGE (a)-[:AUTHORED]->(c)" in cyphers
    assert "MERGE (p)-[:CONTAINS]->(c)" in cyphers


def test_writer_writes_file_changed_in_edge():
    backend = _RecordingBackend()
    w = HistoryWriter(backend, batch_size=10)
    w.write_commits(project="p", commits=[
        _commit(files=(
            FileChange(path="a.py", additions=3, deletions=2),
            FileChange(path="b.py", additions=0, deletions=4),
        )),
    ])
    file_call = next(
        c for c in backend.calls if "(f:File" in c[0] and "CHANGED_IN" in c[0]
    )
    rows = file_call[1]["rows"]
    paths = {r["path"] for r in rows}
    assert paths == {"a.py", "b.py"}


def test_writer_writes_symbol_changed_in_when_attribution_present():
    backend = _RecordingBackend()
    w = HistoryWriter(backend, batch_size=10)
    w.write_commits(project="p", commits=[
        _commit(files=()),
    ], symbol_attributions={
        "a" * 40: {"pkg.foo": 3, "pkg.bar": 7},
    })
    sym_call = next(
        c for c in backend.calls if "(s:Symbol" in c[0] and "CHANGED_IN" in c[0]
    )
    rows = sym_call[1]["rows"]
    by_qn = {r["qualified_name"]: r["lines_overlapped"] for r in rows}
    assert by_qn == {"pkg.foo": 3, "pkg.bar": 7}


def test_writer_updates_last_history_sha():
    backend = _RecordingBackend()
    w = HistoryWriter(backend, batch_size=10)
    w.set_last_history_sha("p", "deadbeef")
    last_call = backend.calls[-1]
    assert "Project" in last_call[0]
    assert last_call[1] == {"project": "p", "sha": "deadbeef"}
```

- [ ] **Step 2: Run them — expect collection error**

- [ ] **Step 3: Implement the writer**

Create `livegraph/history/writer.py`:

```python
"""Batched Cypher writes for the git-history layer."""
from __future__ import annotations

from collections.abc import Iterable

from livegraph.graph.backend import GraphBackend
from livegraph.history.models import CommitRecord


class HistoryWriter:
    """Writes Commit / Author / CHANGED_IN / AUTHORED in batched UNWIND.

    Each call to `write_commits` is idempotent thanks to MERGE-by-sha
    on Commit and MERGE-by-email on Author.
    """

    def __init__(self, backend: GraphBackend, batch_size: int = 500) -> None:
        self._backend = backend
        self._batch_size = batch_size

    def write_commits(
        self,
        project: str,
        commits: Iterable[CommitRecord],
        symbol_attributions: dict[str, dict[str, int]] | None = None,
    ) -> None:
        commits = list(commits)
        if not commits:
            return
        attrs = symbol_attributions or {}

        self._write_authors(commits)
        self._write_commits(project, commits)
        self._write_file_edges(commits)
        self._write_symbol_edges(commits, attrs)

    def _write_authors(self, commits: list[CommitRecord]) -> None:
        rows = [{"email": c.author_email, "name": c.author_name}
                for c in commits]
        self._batched(
            "UNWIND $rows AS row "
            "MERGE (a:Author {email: row.email}) "
            "SET a.name = coalesce(row.name, a.name)",
            rows,
        )

    def _write_commits(self, project: str, commits: list[CommitRecord]) -> None:
        rows = [{
            "sha": c.sha, "short_sha": c.short_sha, "message": c.message,
            "timestamp": c.timestamp, "email": c.author_email,
        } for c in commits]
        self._batched_with_project(
            project,
            "MERGE (p:Project {name: $project}) "
            "WITH p UNWIND $rows AS row "
            "MERGE (c:Commit {sha: row.sha}) "
            "SET c.short_sha = row.short_sha, c.message = row.message, "
            "    c.timestamp = row.timestamp, c.author_email = row.email "
            "MERGE (p)-[:CONTAINS]->(c) "
            "WITH c, row "
            "MATCH (a:Author {email: row.email}) "
            "MERGE (a)-[:AUTHORED]->(c)",
            rows,
        )

    def _write_file_edges(self, commits: list[CommitRecord]) -> None:
        rows = []
        for c in commits:
            for f in c.files:
                rows.append({
                    "sha": c.sha, "path": f.path,
                    "additions": f.additions, "deletions": f.deletions,
                })
        if not rows:
            return
        self._batched(
            "UNWIND $rows AS row "
            "MATCH (c:Commit {sha: row.sha}) "
            "MATCH (f:File {path: row.path}) "
            "MERGE (f)-[e:CHANGED_IN]->(c) "
            "SET e.additions = row.additions, e.deletions = row.deletions",
            rows,
        )

    def _write_symbol_edges(
        self, commits: list[CommitRecord],
        attrs: dict[str, dict[str, int]],
    ) -> None:
        rows = []
        for c in commits:
            for qn, lines in attrs.get(c.sha, {}).items():
                rows.append({
                    "sha": c.sha, "qualified_name": qn,
                    "lines_overlapped": lines,
                })
        if not rows:
            return
        self._batched(
            "UNWIND $rows AS row "
            "MATCH (c:Commit {sha: row.sha}) "
            "MATCH (s:Symbol {qualified_name: row.qualified_name}) "
            "MERGE (s)-[e:CHANGED_IN]->(c) "
            "SET e.lines_overlapped = row.lines_overlapped",
            rows,
        )

    def set_last_history_sha(self, project: str, sha: str) -> None:
        self._backend.execute(
            "MERGE (p:Project {name: $project}) "
            "SET p.last_history_sha = $sha",
            project=project, sha=sha,
        )

    def _batched(self, cypher: str, rows: list[dict]) -> None:
        for start in range(0, len(rows), self._batch_size):
            self._backend.execute(cypher,
                                  rows=rows[start:start + self._batch_size])

    def _batched_with_project(
        self, project: str, cypher: str, rows: list[dict],
    ) -> None:
        for start in range(0, len(rows), self._batch_size):
            self._backend.execute(
                cypher, project=project,
                rows=rows[start:start + self._batch_size],
            )
```

- [ ] **Step 4: Run tests — expect 4 PASS**

- [ ] **Step 5: Commit**

```bash
git add livegraph/history/writer.py tests/unit/test_history_writer.py
git commit -m "feat(phase10): HistoryWriter — batched UNWIND for commits/authors/edges"
```

---

## Task 5: Ingest orchestrator

**Files:**
- Create: `livegraph/history/ingest.py`
- Test: `tests/unit/test_history_ingest.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_history_ingest.py`:

```python
from __future__ import annotations

from typing import Any

from livegraph.history.ingest import IngestHistorySummary, ingest_history
from livegraph.history.models import CommitRecord, FileChange, HunkRange


class _Backend:
    def __init__(self, stored_sha=None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._stored = stored_sha

    def execute(self, cypher: str, **params):
        self.calls.append((cypher, params))
        if "last_history_sha" in cypher and "RETURN" in cypher:
            return [{"sha": self._stored}]
        return []

    def verify(self): return None
    def close(self): return None


def _commit(sha):
    return CommitRecord(
        sha=sha, short_sha=sha[:7], message="m",
        timestamp="2026-01-01T00:00:00+00:00",
        author_email="a@x", author_name="A",
        files=(FileChange(path="a.py", additions=1, deletions=0,
                          hunks=(HunkRange(1, 2),)),),
    )


def test_ingest_writes_commits_and_returns_summary(monkeypatch, tmp_path):
    backend = _Backend()
    commits = [_commit("a" * 40), _commit("b" * 40)]
    monkeypatch.setattr(
        "livegraph.history.ingest.iter_commits",
        lambda *a, **kw: iter(commits),
    )
    monkeypatch.setattr(
        "livegraph.history.ingest.attribute_hunks",
        lambda backend, project, file_path, hunks: {"pkg.foo": 2},
    )
    summary = ingest_history(str(tmp_path), backend, project="p")
    assert isinstance(summary, IngestHistorySummary)
    assert summary.commits == 2
    assert summary.symbol_attributions == 2  # one per commit
    # last_history_sha was set to the HEAD (newest) commit.
    last_set = [c for c in backend.calls
                if "last_history_sha" in c[0] and "SET" in c[0]]
    assert last_set
    assert last_set[-1][1]["sha"] == "a" * 40  # newest first


def test_since_last_reads_stored_sha_and_passes_to_iter(monkeypatch, tmp_path):
    backend = _Backend(stored_sha="cafef00d")
    captured = {}

    def fake_iter(root, since=None, max_commits=None):
        captured["since"] = since
        return iter([])

    monkeypatch.setattr(
        "livegraph.history.ingest.iter_commits", fake_iter,
    )
    ingest_history(str(tmp_path), backend, project="p", since_last=True)
    assert captured["since"] == "cafef00d"


def test_since_last_with_no_stored_sha_falls_back_to_full(
    monkeypatch, tmp_path,
):
    backend = _Backend(stored_sha=None)
    captured = {}

    def fake_iter(root, since=None, max_commits=None):
        captured["since"] = since
        return iter([])

    monkeypatch.setattr(
        "livegraph.history.ingest.iter_commits", fake_iter,
    )
    ingest_history(str(tmp_path), backend, project="p", since_last=True)
    assert captured["since"] is None
```

- [ ] **Step 2: Run them — expect collection error**

- [ ] **Step 3: Implement the orchestrator**

Create `livegraph/history/ingest.py`:

```python
"""Orchestrate the git-history ingest pipeline."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from livegraph.graph.backend import GraphBackend
from livegraph.history.attributor import attribute_hunks
from livegraph.history.extractor import iter_commits
from livegraph.history.writer import HistoryWriter

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestHistorySummary:
    commits: int
    files: int
    symbol_attributions: int


def _read_last_history_sha(backend: GraphBackend, project: str) -> str | None:
    rows = backend.execute(
        "MATCH (p:Project {name: $project}) "
        "RETURN p.last_history_sha AS sha",
        project=project,
    )
    if not rows:
        return None
    sha = rows[0].get("sha")
    return sha or None


def ingest_history(
    root: str,
    backend: GraphBackend,
    project: str,
    since_last: bool = False,
    max_commits: int | None = None,
    batch_size: int = 500,
) -> IngestHistorySummary:
    """Walk git history at `root` and write commit/author/CHANGED_IN
    edges to the project's graph.
    """
    since = None
    if since_last:
        since = _read_last_history_sha(backend, project)
        if since is None:
            log.info("first history ingest for project %r", project)

    commits = list(iter_commits(root, since=since, max_commits=max_commits))
    if not commits:
        return IngestHistorySummary(commits=0, files=0, symbol_attributions=0)

    attributions: dict[str, dict[str, int]] = {}
    n_files = 0
    n_sym_edges = 0
    for c in commits:
        per_commit: dict[str, int] = {}
        for f in c.files:
            n_files += 1
            attributed = attribute_hunks(
                backend, project=project, file_path=f.path, hunks=f.hunks,
            )
            for qn, lines in attributed.items():
                per_commit[qn] = per_commit.get(qn, 0) + lines
        if per_commit:
            attributions[c.sha] = per_commit
            n_sym_edges += len(per_commit)

    writer = HistoryWriter(backend, batch_size=batch_size)
    writer.write_commits(project, commits, symbol_attributions=attributions)

    newest = commits[0].sha  # iter_commits returns newest-first
    writer.set_last_history_sha(project, newest)

    return IngestHistorySummary(
        commits=len(commits), files=n_files,
        symbol_attributions=n_sym_edges,
    )
```

- [ ] **Step 4: Run tests — expect 3 PASS**

- [ ] **Step 5: Commit**

```bash
git add livegraph/history/ingest.py tests/unit/test_history_ingest.py
git commit -m "feat(phase10): ingest_history orchestrator + summary"
```

---

## Task 6: CLI `livegraph ingest-history`

**Files:**
- Modify: `livegraph/cli.py`
- Test: `tests/unit/test_cli_ingest_history.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_cli_ingest_history.py`:

```python
from __future__ import annotations

from typer.testing import CliRunner

from livegraph.cli import app

runner = CliRunner()


def test_ingest_history_help():
    result = runner.invoke(app, ["ingest-history", "--help"])
    assert result.exit_code == 0
    assert "--project" in result.stdout
    assert "--since-last" in result.stdout
    assert "--max-commits" in result.stdout


def test_ingest_history_requires_project(monkeypatch, tmp_path):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    result = runner.invoke(app, ["ingest-history", str(tmp_path)])
    assert result.exit_code == 2


def test_ingest_history_exits_2_on_non_git_dir(monkeypatch, tmp_path):
    from livegraph import cli as cli_mod

    def fake_make_backend():
        b = type("B", (), {})()
        b.verify = lambda: None
        b.close = lambda: None
        return b

    monkeypatch.setattr(cli_mod, "_make_backend", fake_make_backend)
    monkeypatch.setattr(cli_mod, "_resolve_root_path",
                        lambda *a, **kw: str(tmp_path))
    result = runner.invoke(
        app, ["ingest-history", "--project", "p", str(tmp_path)],
    )
    assert result.exit_code == 2
    out = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "git" in out.lower()
```

- [ ] **Step 2: Run them — expect failures (command not registered)**

- [ ] **Step 3: Add the command to `livegraph/cli.py`**

Near the top, add to the existing imports:

```python
from livegraph.history.ingest import ingest_history
```

At the bottom of `cli.py`, after the existing `watch` command, add:

```python
@app.command("ingest-history")
def ingest_history_cmd(
    path: str = typer.Argument(
        None,
        help="Project root (defaults to the Project's stored root_path)",
    ),
    project: str = typer.Option(
        None, "--project",
        help="Ingested project to attach history to (overrides LIVEGRAPH_PROJECT)",
    ),
    since_last: bool = typer.Option(
        False, "--since-last",
        help="Only ingest commits newer than the project's last_history_sha",
    ),
    max_commits: int = typer.Option(
        None, "--max-commits",
        help="Cap the number of commits walked (default: no cap)",
    ),
) -> None:
    """Phase 10: walk git history and attach commit/author edges."""
    import os
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
                f"Pass PATH or re-run `livegraph build`.",
                err=True,
            )
            raise typer.Exit(code=2)
        if not os.path.isdir(os.path.join(resolved_root, ".git")):
            typer.echo(
                f"not a git repository: {resolved_root}",
                err=True,
            )
            raise typer.Exit(code=2)

        summary = ingest_history(
            resolved_root, backend, resolved_project,
            since_last=since_last, max_commits=max_commits,
        )
        typer.echo(
            f"History ingest complete: {summary.commits} commits, "
            f"{summary.files} file changes, "
            f"{summary.symbol_attributions} symbol attributions."
        )
    finally:
        backend.close()
```

- [ ] **Step 4: Run tests — expect 3 PASS**

- [ ] **Step 5: Commit**

```bash
git add livegraph/cli.py tests/unit/test_cli_ingest_history.py
git commit -m "feat(phase10): livegraph ingest-history CLI command"
```

---

## Task 7: MCP tools — `tools_history.py`

**Files:**
- Create: `livegraph/mcp/tools_history.py`
- Test: `tests/unit/test_mcp_tools_history.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mcp_tools_history.py`:

```python
from __future__ import annotations

from typing import Any

from livegraph.mcp.tools_history import (
    recent_changes, symbol_history, top_churn,
)


class _FakeBackend:
    def __init__(self, responses):
        # responses: dict of substring-of-cypher → rows
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher, **params):
        self.calls.append((cypher, params))
        for key, rows in self._responses.items():
            if key in cypher:
                return rows
        return []

    def verify(self): return None
    def close(self): return None


# ---- symbol_history --------------------------------------------------

def test_symbol_history_returns_commits_newest_first():
    backend = _FakeBackend({
        "MATCH (s {qualified_name": [
            {"sha": "b" * 40, "short_sha": "bbbbbbb", "message": "newer",
             "timestamp": "2026-05-15T00:00:00+00:00",
             "author_email": "alice@x", "author_name": "Alice",
             "lines_overlapped": 5},
            {"sha": "a" * 40, "short_sha": "aaaaaaa", "message": "older",
             "timestamp": "2026-04-15T00:00:00+00:00",
             "author_email": "alice@x", "author_name": "Alice",
             "lines_overlapped": 2},
        ],
        "RETURN count": [{"n": 2}],
    })
    result = symbol_history(backend, project="p",
                            qualified_name="pkg.foo", limit=10)
    assert result["warning"] is None
    shas = [c["sha"] for c in result["commits"]]
    assert shas == ["b" * 40, "a" * 40]
    assert result["total_commits"] == 2


def test_symbol_history_warns_when_no_history_ingested():
    # No commit nodes exist for the project at all.
    backend = _FakeBackend({
        "MATCH (s {qualified_name": [],
        "RETURN count": [{"n": 0}],
        "MATCH (:Project {name: $project})-[:CONTAINS]->(:Commit)": [
            {"n": 0},
        ],
    })
    result = symbol_history(backend, project="p", qualified_name="pkg.foo")
    assert result["commits"] == []
    assert "ingest-history" in (result["warning"] or "")


# ---- recent_changes --------------------------------------------------

def test_recent_changes_returns_symbols_ordered_by_last_changed():
    backend = _FakeBackend({
        "ORDER BY last_changed DESC": [
            {"qualified_name": "pkg.foo", "kind": "function",
             "file": "pkg/a.py",
             "last_changed": "2026-05-29T00:00:00+00:00",
             "commit_count": 3, "latest_sha": "abc"},
            {"qualified_name": "pkg.bar", "kind": "method",
             "file": "pkg/b.py",
             "last_changed": "2026-05-20T00:00:00+00:00",
             "commit_count": 1, "latest_sha": "def"},
        ],
    })
    result = recent_changes(backend, project="p", limit=50)
    qns = [r["qualified_name"] for r in result["results"]]
    assert qns == ["pkg.foo", "pkg.bar"]


def test_recent_changes_clamps_limit_to_100():
    backend = _FakeBackend({})
    recent_changes(backend, project="p", limit=9999)
    cyphers = [c for c in backend.calls if "$limit" in c[0]]
    assert cyphers and cyphers[0][1]["limit"] == 100


def test_recent_changes_kind_filter_validated():
    backend = _FakeBackend({})
    result = recent_changes(backend, project="p", kind="garbage")
    assert result["results"] == []
    assert "kind" in (result["warning"] or "").lower()


# ---- top_churn -------------------------------------------------------

def test_top_churn_returns_ranked_results():
    backend = _FakeBackend({
        "ORDER BY commit_count DESC": [
            {"qualified_name": "pkg.foo", "kind": "function",
             "file": "pkg/a.py", "commit_count": 14,
             "unique_authors": 3,
             "first_changed": "2026-05-01T00:00:00+00:00",
             "last_changed": "2026-05-29T00:00:00+00:00"},
        ],
    })
    result = top_churn(backend, project="p", window_days=30)
    assert result["window_days"] == 30
    assert result["results"][0]["commit_count"] == 14
    assert result["results"][0]["unique_authors"] == 3


def test_top_churn_clamps_window_days_to_3650():
    backend = _FakeBackend({})
    result = top_churn(backend, project="p", window_days=99999)
    assert result["window_days"] == 3650
```

- [ ] **Step 2: Run them — expect collection error**

- [ ] **Step 3: Implement the tools**

Create `livegraph/mcp/tools_history.py`:

```python
"""MCP tools that read the git-history layer (Phase 10).

Three tools: ``symbol_history``, ``recent_changes``, ``top_churn``.
"""
from __future__ import annotations

from typing import Any

from livegraph.graph.backend import GraphBackend

_VALID_KINDS = ("any", "function", "method")
_MAX_LIMIT = 100
_MAX_WINDOW_DAYS = 3650  # ~10 years

_HISTORY_INGESTED_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:Commit) "
    "RETURN count(*) AS n LIMIT 1"
)


def _history_present(backend: GraphBackend, project: str) -> bool:
    rows = backend.execute(_HISTORY_INGESTED_CYPHER, project=project)
    if not rows:
        return False
    return int(rows[0].get("n") or 0) > 0


# -- symbol_history ----------------------------------------------------

_SYMBOL_HISTORY_CYPHER = (
    "MATCH (s {qualified_name: $qualified_name})-[e:CHANGED_IN]->(c:Commit) "
    "OPTIONAL MATCH (a:Author)-[:AUTHORED]->(c) "
    "RETURN c.sha AS sha, c.short_sha AS short_sha, "
    "       c.message AS message, c.timestamp AS timestamp, "
    "       c.author_email AS author_email, a.name AS author_name, "
    "       e.lines_overlapped AS lines_overlapped "
    "ORDER BY c.timestamp DESC "
    "LIMIT $limit"
)

_SYMBOL_HISTORY_COUNT = (
    "MATCH (s {qualified_name: $qualified_name})-[:CHANGED_IN]->(:Commit) "
    "RETURN count(*) AS n"
)


def symbol_history(
    backend: GraphBackend, project: str, qualified_name: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Recent commits touching the symbol, newest first."""
    limit = max(1, min(int(limit), _MAX_LIMIT))
    rows = backend.execute(
        _SYMBOL_HISTORY_CYPHER,
        qualified_name=qualified_name, limit=limit,
    )
    count_rows = backend.execute(
        _SYMBOL_HISTORY_COUNT, qualified_name=qualified_name,
    )
    total = int(count_rows[0].get("n") or 0) if count_rows else 0
    warning = None
    if not rows and not _history_present(backend, project):
        warning = (
            "no git history ingested; run `livegraph ingest-history`"
        )
    return {
        "qualified_name": qualified_name,
        "commits": rows,
        "total_commits": total,
        "warning": warning,
    }


# -- recent_changes ----------------------------------------------------

_RECENT_CHANGES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(c:Commit) "
    "WHERE ($since IS NULL OR c.timestamp >= $since) "
    "MATCH (s:Symbol)-[:CHANGED_IN]->(c) "
    "WHERE ($kind = 'any' AND (s:Function OR s:Method)) "
    "   OR ($kind = 'function' AND s:Function AND NOT s:Test) "
    "   OR ($kind = 'method' AND s:Method) "
    "WITH s, max(c.timestamp) AS last_changed, "
    "     count(DISTINCT c) AS commit_count, "
    "     head(collect(c.sha ORDER BY c.timestamp DESC)) AS latest_sha "
    "RETURN s.qualified_name AS qualified_name, "
    "       head([l IN labels(s) "
    "             WHERE l IN ['Function','Method'] | toLower(l)]) AS kind, "
    "       s.file AS file, "
    "       last_changed, commit_count, latest_sha "
    "ORDER BY last_changed DESC "
    "LIMIT $limit"
)


def recent_changes(
    backend: GraphBackend, project: str,
    since: str | None = None, limit: int = 50, kind: str = "any",
) -> dict[str, Any]:
    """Symbols changed in commits with timestamp >= since."""
    if kind not in _VALID_KINDS:
        return {
            "results": [],
            "warning": (
                f"invalid kind {kind!r}; "
                f"must be one of 'any', 'function', 'method'"
            ),
        }
    limit = max(1, min(int(limit), _MAX_LIMIT))
    rows = backend.execute(
        _RECENT_CHANGES_CYPHER,
        project=project, since=since, kind=kind, limit=limit,
    )
    warning = None
    if not rows and not _history_present(backend, project):
        warning = "no git history ingested; run `livegraph ingest-history`"
    return {"results": rows, "warning": warning}


# -- top_churn ---------------------------------------------------------

_TOP_CHURN_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(c:Commit) "
    "WHERE c.timestamp >= $cutoff "
    "MATCH (s:Symbol)-[:CHANGED_IN]->(c) "
    "OPTIONAL MATCH (a:Author)-[:AUTHORED]->(c) "
    "WHERE ($kind = 'any' AND (s:Function OR s:Method)) "
    "   OR ($kind = 'function' AND s:Function AND NOT s:Test) "
    "   OR ($kind = 'method' AND s:Method) "
    "WITH s, "
    "     count(DISTINCT c) AS commit_count, "
    "     count(DISTINCT a) AS unique_authors, "
    "     min(c.timestamp) AS first_changed, "
    "     max(c.timestamp) AS last_changed "
    "RETURN s.qualified_name AS qualified_name, "
    "       head([l IN labels(s) "
    "             WHERE l IN ['Function','Method'] | toLower(l)]) AS kind, "
    "       s.file AS file, "
    "       commit_count, unique_authors, first_changed, last_changed "
    "ORDER BY commit_count DESC, last_changed DESC "
    "LIMIT $limit"
)


def top_churn(
    backend: GraphBackend, project: str,
    window_days: int = 30, limit: int = 20, kind: str = "any",
) -> dict[str, Any]:
    """Top-K symbols by distinct commits in the window."""
    if kind not in _VALID_KINDS:
        return {
            "window_days": window_days, "results": [],
            "warning": (
                f"invalid kind {kind!r}; "
                f"must be one of 'any', 'function', 'method'"
            ),
        }
    window_days = max(1, min(int(window_days), _MAX_WINDOW_DAYS))
    limit = max(1, min(int(limit), _MAX_LIMIT))
    cutoff_iso = _cutoff_iso(window_days)
    rows = backend.execute(
        _TOP_CHURN_CYPHER,
        project=project, cutoff=cutoff_iso, kind=kind, limit=limit,
    )
    warning = None
    if not rows and not _history_present(backend, project):
        warning = "no git history ingested; run `livegraph ingest-history`"
    return {"window_days": window_days, "results": rows, "warning": warning}


def _cutoff_iso(window_days: int) -> str:
    import datetime
    cutoff = datetime.datetime.now(datetime.timezone.utc) - \
        datetime.timedelta(days=window_days)
    return cutoff.isoformat()
```

- [ ] **Step 4: Run tests — expect 8 PASS**

```
.venv/bin/python -m pytest tests/unit/test_mcp_tools_history.py -v
```

- [ ] **Step 5: Commit**

```bash
git add livegraph/mcp/tools_history.py tests/unit/test_mcp_tools_history.py
git commit -m "feat(phase10): MCP tools — symbol_history, recent_changes, top_churn"
```

---

## Task 8: Register the 3 new MCP tools

**Files:**
- Modify: `livegraph/mcp/server.py`
- Modify: `tests/integration/test_mcp_server_smoke.py`

- [ ] **Step 1: Add the import to `server.py`**

Near the existing `from livegraph.mcp.tools_neighborhood import ...` line, add:

```python
from livegraph.mcp.tools_history import (
    recent_changes as _recent_changes,
    symbol_history as _symbol_history,
    top_churn as _top_churn,
)
```

- [ ] **Step 2: Register the 3 tools inside `build_server`**

Just before `return mcp`, add:

```python
    @mcp.tool()
    def symbol_history(
        qualified_name: str, limit: int = 20,
    ) -> dict[str, Any]:
        """Recent commits that touched ``qualified_name``, newest first.

        Returns ``{qualified_name, commits, total_commits, warning}``.
        ``commits`` carries sha/short_sha/message/timestamp/author and
        the per-symbol lines_overlapped credit.
        """
        backend, project = _require_state()
        return _symbol_history(backend, project,
                               qualified_name=qualified_name, limit=limit)

    @mcp.tool()
    def recent_changes(
        since: str | None = None, limit: int = 50, kind: str = "any",
    ) -> dict[str, Any]:
        """Symbols changed in commits with timestamp >= ``since`` (ISO-8601).

        If ``since`` is null, returns the most recent ``limit`` symbols
        by ``last_changed``. ``kind`` is ``"any"``, ``"function"``, or
        ``"method"``.
        """
        backend, project = _require_state()
        return _recent_changes(backend, project,
                               since=since, limit=limit, kind=kind)

    @mcp.tool()
    def top_churn(
        window_days: int = 30, limit: int = 20, kind: str = "any",
    ) -> dict[str, Any]:
        """Top-K symbols by distinct commits in the last ``window_days``.

        Returns ``{window_days, results, warning}`` ordered by
        ``commit_count DESC, last_changed DESC``.
        """
        backend, project = _require_state()
        return _top_churn(backend, project,
                          window_days=window_days, limit=limit, kind=kind)
```

- [ ] **Step 3: Update "15 tools" → "18 tools" in server.py docstrings**

Find and update both occurrences (module docstring and `build_server` docstring).

- [ ] **Step 4: Update the smoke test's expected tool list**

In `tests/integration/test_mcp_server_smoke.py`, add the three names:

```python
            "symbol_history", "recent_changes", "top_churn",
```

to the sorted list (so it now has 18 entries).

- [ ] **Step 5: Run the server unit tests + smoke test**

```
.venv/bin/python -m pytest tests/unit/test_mcp_server.py -v
```

The existing `test_build_server_registers_fifteen_tools_...` test will fail because the list has grown. Rename it to `..._eighteen_tools_...` and add the 3 new names to the expected list. (This mirrors how Phase 9's review updated the same test from 14 → 15.)

```
.venv/bin/python -m pytest tests/unit/test_mcp_server.py tests/integration/test_mcp_server_smoke.py -v
```
Expected: all PASS (smoke test may skip if Neo4j isn't up; that's fine).

- [ ] **Step 6: Commit**

```bash
git add livegraph/mcp/server.py tests/unit/test_mcp_server.py tests/integration/test_mcp_server_smoke.py
git commit -m "feat(phase10): register 3 history tools (16-18)"
```

---

## Task 9: Integration test

**Files:**
- Create: `tests/integration/test_history_integration.py`

- [ ] **Step 1: Write the test file**

```python
"""End-to-end: real git repo + real Neo4j + history tools."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _git(repo: Path, *args, author=("Alice", "alice@x")):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
        "GIT_COMMITTER_NAME": author[0], "GIT_COMMITTER_EMAIL": author[1],
        "GIT_AUTHOR_DATE": "2026-05-01T00:00:00",
        "GIT_COMMITTER_DATE": "2026-05-01T00:00:00",
    }
    subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.fixture()
def history_project(neo4j_backend, tmp_path):
    """Build a tiny git repo, ingest it (Phase 1) + ingest its history."""
    from livegraph.history.ingest import ingest_history
    from livegraph.ingest import ingest_project

    backend = neo4j_backend
    project = "history_test"

    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "alice@x")
    _git(tmp_path, "config", "user.name", "Alice")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "a.py").write_text(
        "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    )
    _git(tmp_path, "add", "pkg")
    _git(tmp_path, "commit", "-q", "-m", "add foo + bar")

    (tmp_path / "pkg" / "a.py").write_text(
        "def foo():\n    return 42\n\ndef bar():\n    return 2\n"
    )
    _git(tmp_path, "add", "pkg/a.py")
    _git(tmp_path, "commit", "-q", "-m", "tweak foo",
         author=("Bob", "bob@x"))

    ingest_project(str(tmp_path), backend, project_name=project)
    summary = ingest_history(str(tmp_path), backend, project)
    assert summary.commits == 2
    return backend, project


def test_symbol_history_returns_commits_for_foo(history_project):
    from livegraph.mcp.tools_history import symbol_history

    backend, project = history_project
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->"
        "(:File {path: 'pkg/a.py'})-[:DEFINES]->(s:Function {name: 'foo'}) "
        "RETURN s.qualified_name AS qn",
        project=project,
    )
    foo_qn = rows[0]["qn"]

    result = symbol_history(backend, project, qualified_name=foo_qn, limit=10)
    assert result["warning"] is None
    # foo was touched in both commits.
    assert result["total_commits"] >= 2
    messages = [c["message"] for c in result["commits"]]
    assert any("tweak foo" in m for m in messages)


def test_recent_changes_lists_foo(history_project):
    from livegraph.mcp.tools_history import recent_changes

    backend, project = history_project
    result = recent_changes(backend, project, limit=50)
    qns = [r["qualified_name"] for r in result["results"]]
    assert any(q.endswith(".foo") for q in qns)


def test_top_churn_ranks_foo_above_bar(history_project):
    from livegraph.mcp.tools_history import top_churn

    backend, project = history_project
    result = top_churn(backend, project, window_days=3650, limit=10)
    # foo was changed in 2 commits, bar in 1. foo should rank first.
    qns_in_order = [r["qualified_name"] for r in result["results"]]
    foo_idx = next(i for i, q in enumerate(qns_in_order) if q.endswith(".foo"))
    bar_idxs = [i for i, q in enumerate(qns_in_order) if q.endswith(".bar")]
    if bar_idxs:
        assert foo_idx < bar_idxs[0]
```

- [ ] **Step 2: Run with Neo4j running**

```
.venv/bin/python -m pytest tests/integration/test_history_integration.py -v -m integration
```
Expected: 3 PASS (or skip if Neo4j unreachable).

If the tests fail in a way that indicates a real bug (not an env issue), pause and report — do NOT loosen the assertions.

- [ ] **Step 3: Run the full suite to confirm no regressions**

```
.venv/bin/python -m pytest -q
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_history_integration.py
git commit -m "test(phase10): git history ingest + tools end-to-end"
```

---

## Task 10: README section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a new section to the README**

After the existing "Semantic neighborhood" section, add:

```markdown

## Git history (`livegraph ingest-history`)

Phase 10 attaches a time/authorship axis to the graph. Walk the
project's git history into `(:Commit)` / `(:Author)` nodes with
`CHANGED_IN` edges (file-level always; symbol-level when commit hunks
overlap the symbol's current source lines).

```bash
livegraph ingest-history --project myproj /path/to/repo
livegraph ingest-history --project myproj --since-last   # incremental
```

Three new MCP tools (bringing the count to 18):

| Tool | What it answers |
|---|---|
| `symbol_history(qualified_name, limit)` | Recent commits + authors that touched a symbol. |
| `recent_changes(since, limit, kind)` | Symbols changed in commits since a timestamp. |
| `top_churn(window_days, limit, kind)` | Hotspot symbols ranked by commit count. |

Caveats:
- Attribution uses the *current* parse's line ranges, so symbols whose
  lines moved a lot through history may be over- or under-credited for
  old commits. Recent commits are accurate.
- Author identity keys on email; respect `.mailmap` if present.
- `livegraph update` and `livegraph watch` leave history edges alone.
  Run `ingest-history --since-last` after a long editing session to
  catch up.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(phase10): README section for git-history layer"
```

---

## Acceptance gate (manual, before PR)

- [ ] `.venv/bin/python -m pytest -q` → all unit + integration tests pass.
- [ ] `.venv/bin/python -m ruff check .` → no new errors compared to main.
- [ ] Manual smoke: in a real-ish git repo (livegraph itself works), run `livegraph ingest-history --project livegraph` and confirm Commit/Author nodes appear. Call `top_churn(30, 5)` over MCP and confirm a sensible hotspot list.
- [ ] Manual `--since-last`: run `ingest-history` a second time with `--since-last` and confirm it reports 0 new commits.
