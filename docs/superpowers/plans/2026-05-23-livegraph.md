# livegraph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `livegraph` Phase 1 (static code graph) and Phase 2 (runtime augmentation): a Python CLI that ingests a Python codebase into a Neo4j knowledge graph and enriches it with real call edges and per-test coverage observed by tracing the target's pytest suite.

**Architecture:** Two phases write to one graph behind a `GraphBackend` adapter. Phase 1 parses every `.py` file with tree-sitter and writes `File`/`Class`/`Function`/`Method`/`Module` nodes plus `CONTAINS`/`DEFINES`/`HAS_METHOD`/`IMPORTS`/`CALLS` edges (provenance `static`). Phase 2 runs the target's pytest suite under a `livegraph` pytest plugin that uses `sys.monitoring` for call edges and `coverage.py` dynamic contexts for per-test coverage, dumps observations to JSON, and merges them — setting `runtime` provenance, adding `:Test` labels, and writing `COVERS` edges. Node identity is a shared `qualified_name` so both phases address the same nodes.

**Tech Stack:** Python 3.12+, `tree-sitter` + `tree-sitter-python`, `neo4j` driver + `neo4j-rust-ext`, `coverage`, `pytest`, `sys.monitoring` (PEP 669), `pydantic` / `pydantic-settings`, `typer`. Neo4j runs via Docker Compose. Strict `mypy`, `ruff`.

**Reference:** Design spec at `docs/superpowers/specs/2026-05-23-livegraph-design.md`.

**Conventions for every task:**
- Run tests from the repo root: `cd ~/livegraph`.
- Unit tests need no Docker. Integration tests are marked `@pytest.mark.integration` and need Neo4j running (`docker compose up -d`).
- Commit after each task with the shown message.

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `docker-compose.yml`
- Create: `README.md`
- Create: `livegraph/__init__.py`
- Create: `livegraph/graph/__init__.py`
- Create: `livegraph/static/__init__.py`
- Create: `livegraph/runtime/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "livegraph"
version = "0.1.0"
description = "A runtime-augmented code knowledge graph for Python codebases."
requires-python = ">=3.12"
dependencies = [
    "tree-sitter>=0.23",
    "tree-sitter-python>=0.23",
    "neo4j>=5.26",
    "neo4j-rust-ext",
    "coverage>=7.6",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "typer>=0.15",
]

[project.scripts]
livegraph = "livegraph.cli:app"

[project.optional-dependencies]
dev = ["pytest>=8.3", "mypy>=1.13", "ruff>=0.8"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["livegraph*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["integration: requires a running Neo4j (deselect with -m 'not integration')"]

[tool.mypy]
python_version = "3.12"
strict = true

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
.env
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage*
build/
dist/
```

- [ ] **Step 3: Create `.env.example`**

```dotenv
# Copy to .env and adjust. Values below match docker-compose.yml.
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=livegraph-local
LIVEGRAPH_BATCH_SIZE=1000
LIVEGRAPH_LOG_LEVEL=INFO
```

- [ ] **Step 4: Create `docker-compose.yml`**

```yaml
services:
  neo4j:
    image: neo4j:5.26
    container_name: livegraph-neo4j
    ports:
      - "7474:7474"   # Neo4j Browser
      - "7687:7687"   # Bolt
    environment:
      NEO4J_AUTH: neo4j/livegraph-local
    volumes:
      - livegraph_neo4j_data:/data

volumes:
  livegraph_neo4j_data:
```

- [ ] **Step 5: Create `README.md`**

```markdown
# livegraph

A runtime-augmented code knowledge graph for Python codebases.

`livegraph` builds a graph of a Python project in Neo4j, fusing static
tree-sitter analysis with runtime observation of the project's pytest
suite. Every `CALLS` edge is tagged with provenance (`static` / `runtime`)
so the graph reflects what the code *does*, not just what it looks like.

## Quick start

    docker compose up -d
    cp .env.example .env
    pip install -e ".[dev]"
    livegraph build /path/to/python/project

See `docs/superpowers/specs/` for the design.
```

- [ ] **Step 6: Create the seven empty `__init__.py` files**

Each file is created empty (zero bytes): `livegraph/__init__.py`, `livegraph/graph/__init__.py`, `livegraph/static/__init__.py`, `livegraph/runtime/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`. Also create `livegraph/queries/` by creating the directory (a `.scm` file is added in Task 9).

- [ ] **Step 7: Create the virtualenv and install**

Run:
```bash
cd ~/livegraph
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```
Expected: install completes without error.

- [ ] **Step 8: Verify pytest collects nothing yet**

Run: `cd ~/livegraph && .venv/bin/pytest -q`
Expected: `no tests ran` (exit code 5) — confirms the harness is wired.

- [ ] **Step 9: Commit**

```bash
cd ~/livegraph
git add -A
git commit -m "chore: project scaffolding, packaging, docker-compose"
```

---

## Task 2: Configuration (`config.py`)

**Files:**
- Create: `livegraph/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config.py
from livegraph.config import Settings


def test_defaults_when_no_env(monkeypatch):
    for var in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
                "LIVEGRAPH_BATCH_SIZE", "LIVEGRAPH_LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.neo4j_uri == "bolt://localhost:7687"
    assert settings.neo4j_user == "neo4j"
    assert settings.livegraph_batch_size == 1000


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://example:9999")
    monkeypatch.setenv("LIVEGRAPH_BATCH_SIZE", "50")
    settings = Settings(_env_file=None)
    assert settings.neo4j_uri == "bolt://example:9999"
    assert settings.livegraph_batch_size == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.config'`.

- [ ] **Step 3: Write `livegraph/config.py`**

```python
"""Typed configuration loaded from the environment / a .env file."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for livegraph.

    Field names map case-insensitively to the uppercase env vars
    (e.g. ``neo4j_uri`` <- ``NEO4J_URI``).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "livegraph-local"
    livegraph_batch_size: int = 1000
    livegraph_log_level: str = "INFO"


def load_settings() -> Settings:
    """Return a Settings instance built from the environment and .env."""
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/config.py tests/unit/test_config.py
git commit -m "feat: typed configuration via pydantic-settings"
```

---

## Task 3: Data models (`models.py`)

**Files:**
- Create: `livegraph/models.py`
- Test: `tests/unit/test_models.py`

These frozen dataclasses are the typed records every other module exchanges. `kind` on `Definition` is one of `"function"`, `"class"`, `"method"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_models.py
from livegraph.models import Definition, FileRecord, ImportRecord, CallEdge


def test_file_record_defaults():
    f = FileRecord(path="a/b.py", name="b.py")
    assert f.language == "python"
    assert f.parse_error is False


def test_definition_is_frozen():
    d = Definition(qualified_name="a.py::f", name="f", kind="function",
                   file="a.py", start_line=1, end_line=2,
                   decorators=(), source="def f(): pass")
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.name = "g"  # type: ignore[misc]


def test_call_edge_defaults():
    c = CallEdge(caller_qn="a.py::f", callee_qn="a.py::g")
    assert c.static is False and c.runtime is False
    assert c.observed_count == 0 and c.call_site_lines == ()


def test_import_record():
    i = ImportRecord(file="a.py", raw="import os", line=1, module="os")
    assert i.module == "os"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.models'`.

- [ ] **Step 3: Write `livegraph/models.py`**

```python
"""Typed records exchanged between livegraph's modules.

Phase-1 (static) records only. Runtime records are added in a later task.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FileRecord:
    """A source file discovered during ingestion."""

    path: str            # project-relative, forward-slash separated
    name: str            # basename
    language: str = "python"
    parse_error: bool = False


@dataclass(frozen=True, slots=True)
class Definition:
    """A class, function, or method definition extracted from an AST."""

    qualified_name: str
    name: str
    kind: str            # "function" | "class" | "method"
    file: str            # project-relative path
    start_line: int
    end_line: int
    decorators: tuple[str, ...]
    source: str
    parent_class: str | None = None   # qualified_name of the owning class


@dataclass(frozen=True, slots=True)
class ImportRecord:
    """A single import statement, before resolution."""

    file: str            # importing file, project-relative
    raw: str             # raw statement text
    line: int
    module: str          # dotted module name being imported


@dataclass(frozen=True, slots=True)
class CallEdge:
    """A call relationship between two definitions, with provenance."""

    caller_qn: str
    callee_qn: str
    static: bool = False
    runtime: bool = False
    observed_count: int = 0
    call_site_lines: tuple[int, ...] = field(default=())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_models.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/models.py tests/unit/test_models.py
git commit -m "feat: static-phase data models"
```

---

## Task 4: Qualified-name identity (`qualnames.py`)

**Files:**
- Create: `livegraph/qualnames.py`
- Test: `tests/unit/test_qualnames.py`

This module is the join between Phase 1 and Phase 2. Both phases must produce the *same* `qualified_name` string for the same symbol. It is the highest-risk component, so it gets thorough tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_qualnames.py
from livegraph.qualnames import (
    file_qid, symbol_qid, normalize_co_qualname, rel_path,
)


def test_file_qid_normalizes_separators():
    assert file_qid("src\\app\\h.py") == "src/app/h.py"


def test_symbol_qid_for_function():
    assert symbol_qid("src/app/h.py", "process") == "src/app/h.py::process"


def test_symbol_qid_for_method():
    assert symbol_qid("a.py", "Handler.run") == "a.py::Handler.run"


def test_normalize_strips_locals_segments():
    assert normalize_co_qualname("outer.<locals>.inner") == "outer.inner"
    assert normalize_co_qualname("Handler.run") == "Handler.run"
    assert normalize_co_qualname("plain") == "plain"


def test_rel_path_within_root(tmp_path):
    root = tmp_path
    f = root / "pkg" / "mod.py"
    f.parent.mkdir(parents=True)
    f.touch()
    assert rel_path(str(f), str(root)) == "pkg/mod.py"


def test_rel_path_outside_root_returns_none(tmp_path):
    assert rel_path("/somewhere/else/x.py", str(tmp_path)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_qualnames.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.qualnames'`.

- [ ] **Step 3: Write `livegraph/qualnames.py`**

```python
"""Stable symbol identity shared by the static and runtime phases.

A symbol's ``qualified_name`` is ``<relative-path>::<dotted-name>``,
e.g. ``src/app/h.py::Handler.run``. Phase 1 builds it from the AST;
Phase 2 builds the identical string from a runtime code object.
"""
from __future__ import annotations

import os


def file_qid(rel: str) -> str:
    """Normalize a relative path to a forward-slash file identifier."""
    return rel.replace("\\", "/")


def symbol_qid(rel: str, dotted_name: str) -> str:
    """Build a symbol qualified_name from its file and dotted name.

    ``dotted_name`` is the AST-nesting path, e.g. ``Handler.run`` for a
    method or ``process`` for a module-level function.
    """
    return f"{file_qid(rel)}::{dotted_name}"


def normalize_co_qualname(co_qualname: str) -> str:
    """Strip CPython ``<locals>`` segments from a code object qualname.

    ``outer.<locals>.inner`` -> ``outer.inner`` so a runtime qualname
    lines up with the dotted name Phase 1 derives from AST nesting.
    """
    return ".".join(part for part in co_qualname.split(".") if part != "<locals>")


def rel_path(abs_path: str, root: str) -> str | None:
    """Return ``abs_path`` relative to ``root`` (forward slashes), or None.

    None means the path is outside the project root and should be ignored.
    """
    try:
        rel = os.path.relpath(os.path.realpath(abs_path), os.path.realpath(root))
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return file_qid(rel)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_qualnames.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/qualnames.py tests/unit/test_qualnames.py
git commit -m "feat: shared qualified-name identity for static/runtime join"
```

---

## Task 5: Graph backend adapter (`graph/backend.py`)

**Files:**
- Create: `livegraph/graph/backend.py`
- Test: `tests/unit/test_backend.py`

`GraphBackend` is the swappable interface. `Neo4jBackend` is the v1 implementation. The unit test exercises a tiny in-memory fake to prove the interface contract; the real Neo4j path is exercised by integration tests later.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_backend.py
from livegraph.graph.backend import GraphBackend, FakeBackend


def test_fake_backend_records_calls():
    backend: GraphBackend = FakeBackend()
    backend.execute("MERGE (n:Test {id: $id})", id="x")
    assert backend.calls == [("MERGE (n:Test {id: $id})", {"id": "x"})]


def test_fake_backend_returns_canned_rows():
    backend = FakeBackend(rows=[{"count": 3}])
    assert backend.execute("MATCH (n) RETURN count(n) AS count") == [{"count": 3}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_backend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.graph.backend'`.

- [ ] **Step 3: Write `livegraph/graph/backend.py`**

```python
"""Swappable graph-database backend.

All database access goes through ``GraphBackend``. v1 ships ``Neo4jBackend``;
``FakeBackend`` exists for unit tests. Swapping the backend later (e.g. to an
embedded database) means adding one class here and nothing else changes.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GraphBackend(Protocol):
    """Minimal interface every graph backend must provide."""

    def verify(self) -> None:
        """Raise if the database is unreachable."""

    def execute(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """Run a Cypher query and return rows as plain dicts."""

    def close(self) -> None:
        """Release all resources."""


class Neo4jBackend:
    """``GraphBackend`` backed by a Neo4j Bolt connection."""

    def __init__(self, uri: str, user: str, password: str,
                 database: str = "neo4j") -> None:
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    def verify(self) -> None:
        from neo4j.exceptions import Neo4jError, ServiceUnavailable

        try:
            self._driver.verify_connectivity()
        except (ServiceUnavailable, Neo4jError) as exc:  # pragma: no cover
            raise ConnectionError(f"Neo4j unreachable: {exc}") from exc

    def execute(self, query: str, **params: Any) -> list[dict[str, Any]]:
        records, _summary, _keys = self._driver.execute_query(
            query, database_=self._database, **params,
        )
        return [record.data() for record in records]

    def close(self) -> None:
        self._driver.close()


class FakeBackend:
    """In-memory ``GraphBackend`` for unit tests.

    Records every ``execute`` call and returns canned rows.
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rows = rows or []

    def verify(self) -> None:
        return None

    def execute(self, query: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        return list(self._rows)

    def close(self) -> None:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_backend.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/graph/backend.py tests/unit/test_backend.py
git commit -m "feat: swappable GraphBackend adapter with Neo4j and fake impls"
```

---

## Task 6: Graph schema setup (`graph/schema.py`)

**Files:**
- Create: `livegraph/graph/schema.py`
- Test: `tests/unit/test_schema.py`

`schema.py` holds label/edge name constants and `create_schema()`, which issues idempotent `CREATE CONSTRAINT ... IF NOT EXISTS` statements. A uniqueness constraint in Neo4j auto-creates its backing index, so no separate index statements are needed.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_schema.py
from livegraph.graph.backend import FakeBackend
from livegraph.graph.schema import create_schema, NODE_KEYS


def test_create_schema_issues_a_constraint_per_keyed_label():
    backend = FakeBackend()
    create_schema(backend)
    issued = [q for q, _ in backend.calls]
    assert len(issued) == len(NODE_KEYS)
    assert all("CONSTRAINT" in q and "IF NOT EXISTS" in q for q in issued)


def test_create_schema_covers_expected_labels():
    backend = FakeBackend()
    create_schema(backend)
    issued = " ".join(q for q, _ in backend.calls)
    for label in ("Project", "File", "Module", "Class", "Function", "Method"):
        assert f":{label}" in issued
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.graph.schema'`.

- [ ] **Step 3: Write `livegraph/graph/schema.py`**

```python
"""Graph schema: label/edge constants and constraint setup."""
from __future__ import annotations

from livegraph.graph.backend import GraphBackend

# Node labels.
PROJECT = "Project"
FILE = "File"
MODULE = "Module"
CLASS = "Class"
FUNCTION = "Function"
METHOD = "Method"
TEST = "Test"          # an extra label added to Function nodes in Phase 2

# Edge types.
CONTAINS = "CONTAINS"
DEFINES = "DEFINES"
HAS_METHOD = "HAS_METHOD"
IMPORTS = "IMPORTS"
CALLS = "CALLS"
COVERS = "COVERS"

# The unique key property for every keyed node label.
NODE_KEYS: dict[str, str] = {
    PROJECT: "name",
    FILE: "path",
    MODULE: "name",
    CLASS: "qualified_name",
    FUNCTION: "qualified_name",
    METHOD: "qualified_name",
}


def create_schema(backend: GraphBackend) -> None:
    """Create one uniqueness constraint per keyed label (idempotent).

    A Neo4j uniqueness constraint auto-creates a backing index, so this
    also makes MERGE on the key fast.
    """
    for label, key in NODE_KEYS.items():
        constraint = f"livegraph_{label.lower()}_{key}"
        backend.execute(
            f"CREATE CONSTRAINT {constraint} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_schema.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/graph/schema.py tests/unit/test_schema.py
git commit -m "feat: graph schema constants and constraint setup"
```

---

## Task 7: Batched graph writer (`graph/writer.py`)

**Files:**
- Create: `livegraph/graph/writer.py`
- Test: `tests/unit/test_writer.py`

`writer.py` turns model records into batched `UNWIND $rows ... MERGE` Cypher. Every write is idempotent (MERGE on the unique key). It is verified here against `FakeBackend`; correctness against real Neo4j is covered by Task 13.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_writer.py
from livegraph.graph.backend import FakeBackend
from livegraph.graph.writer import GraphWriter
from livegraph.models import FileRecord, Definition, CallEdge


def test_write_files_batches_by_size():
    backend = FakeBackend()
    writer = GraphWriter(backend, batch_size=2)
    files = [FileRecord(path=f"f{i}.py", name=f"f{i}.py") for i in range(5)]
    writer.write_files("proj", files)
    # 5 files at batch size 2 -> 3 UNWIND calls.
    file_calls = [c for c in backend.calls if "File" in c[0]]
    assert len(file_calls) == 3
    assert all("UNWIND" in q and "MERGE" in q for q, _ in file_calls)


def test_write_files_passes_rows_as_param():
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).write_files(
        "proj", [FileRecord(path="a.py", name="a.py")]
    )
    _query, params = backend.calls[0]
    assert params["rows"] == [
        {"path": "a.py", "name": "a.py", "language": "python", "parse_error": False}
    ]
    assert params["project"] == "proj"


def test_write_definitions_routes_methods_to_method_label():
    backend = FakeBackend()
    defs = [
        Definition("a.py::C", "C", "class", "a.py", 1, 9, (), "class C: ..."),
        Definition("a.py::C.m", "m", "method", "a.py", 2, 3, (), "def m(self): ...",
                   parent_class="a.py::C"),
        Definition("a.py::f", "f", "function", "a.py", 11, 12, (), "def f(): ..."),
    ]
    GraphWriter(backend, batch_size=100).write_definitions(defs)
    issued = " ".join(q for q, _ in backend.calls)
    assert ":Class" in issued and ":Method" in issued and ":Function" in issued
    assert "HAS_METHOD" in issued


def test_write_calls_emits_provenance_properties():
    backend = FakeBackend()
    edges = [CallEdge("a.py::f", "a.py::g", static=True)]
    GraphWriter(backend, batch_size=100).write_calls(edges)
    query, params = backend.calls[0]
    assert "MERGE" in query and ":CALLS" in query
    assert params["rows"][0]["static"] is True
    assert params["rows"][0]["runtime"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.graph.writer'`.

- [ ] **Step 3: Write `livegraph/graph/writer.py`**

```python
"""Batched, idempotent Cypher writes for livegraph records."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, TypeVar

from livegraph.graph.backend import GraphBackend
from livegraph.models import CallEdge, Definition, FileRecord

_T = TypeVar("_T")

# Label per Definition.kind.
_DEF_LABEL = {"class": "Class", "function": "Function", "method": "Method"}


def _batched(items: Iterable[_T], size: int) -> Iterator[list[_T]]:
    """Yield ``items`` in chunks of at most ``size``."""
    batch: list[_T] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


class GraphWriter:
    """Writes model records to a ``GraphBackend`` in idempotent batches."""

    def __init__(self, backend: GraphBackend, batch_size: int = 1000) -> None:
        self._backend = backend
        self._batch_size = batch_size

    def write_files(self, project: str, files: Iterable[FileRecord]) -> None:
        """MERGE File nodes and CONTAINS edges from the Project node."""
        for batch in _batched(files, self._batch_size):
            rows = [
                {"path": f.path, "name": f.name,
                 "language": f.language, "parse_error": f.parse_error}
                for f in batch
            ]
            self._backend.execute(
                "MERGE (p:Project {name: $project}) "
                "WITH p UNWIND $rows AS row "
                "MERGE (f:File {path: row.path}) "
                "SET f.name = row.name, f.language = row.language, "
                "    f.parse_error = row.parse_error "
                "MERGE (p)-[:CONTAINS]->(f)",
                project=project, rows=rows,
            )

    def write_definitions(self, definitions: Iterable[Definition]) -> None:
        """MERGE Class/Function/Method nodes with their structural edges."""
        for batch in _batched(definitions, self._batch_size):
            for kind, label in _DEF_LABEL.items():
                rows = [self._def_row(d) for d in batch if d.kind == kind]
                if not rows:
                    continue
                if kind == "method":
                    self._write_methods(rows)
                else:
                    self._write_file_definitions(label, rows)

    def write_calls(self, edges: Iterable[CallEdge]) -> None:
        """MERGE CALLS edges, setting provenance properties."""
        for batch in _batched(edges, self._batch_size):
            rows = [
                {"caller": e.caller_qn, "callee": e.callee_qn,
                 "static": e.static, "runtime": e.runtime,
                 "observed_count": e.observed_count,
                 "call_site_lines": list(e.call_site_lines)}
                for e in batch
            ]
            self._backend.execute(
                "UNWIND $rows AS row "
                "MATCH (caller {qualified_name: row.caller}) "
                "MATCH (callee {qualified_name: row.callee}) "
                "MERGE (caller)-[c:CALLS]->(callee) "
                "SET c.static = row.static, c.runtime = row.runtime, "
                "    c.observed_count = row.observed_count, "
                "    c.call_site_lines = row.call_site_lines",
                rows=rows,
            )

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _def_row(d: Definition) -> dict[str, Any]:
        return {
            "qualified_name": d.qualified_name, "name": d.name, "file": d.file,
            "start_line": d.start_line, "end_line": d.end_line,
            "decorators": list(d.decorators), "source": d.source,
            "parent_class": d.parent_class,
        }

    def _write_file_definitions(self, label: str, rows: list[dict[str, Any]]) -> None:
        self._backend.execute(
            f"UNWIND $rows AS row "
            f"MATCH (file:File {{path: row.file}}) "
            f"MERGE (d:{label} {{qualified_name: row.qualified_name}}) "
            f"SET d.name = row.name, d.file = row.file, "
            f"    d.start_line = row.start_line, d.end_line = row.end_line, "
            f"    d.decorators = row.decorators, d.source = row.source "
            f"MERGE (file)-[:DEFINES]->(d)",
            rows=rows,
        )

    def _write_methods(self, rows: list[dict[str, Any]]) -> None:
        self._backend.execute(
            "UNWIND $rows AS row "
            "MATCH (cls:Class {qualified_name: row.parent_class}) "
            "MERGE (m:Method {qualified_name: row.qualified_name}) "
            "SET m.name = row.name, m.file = row.file, "
            "    m.start_line = row.start_line, m.end_line = row.end_line, "
            "    m.decorators = row.decorators, m.source = row.source, "
            "    m.class = row.parent_class "
            "MERGE (cls)-[:HAS_METHOD]->(m)",
            rows=rows,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_writer.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/graph/writer.py tests/unit/test_writer.py
git commit -m "feat: batched idempotent graph writer"
```

---

## Task 8: tree-sitter parser (`static/parser.py`)

**Files:**
- Create: `livegraph/static/parser.py`
- Test: `tests/unit/test_parser.py`

`parser.py` wraps tree-sitter: one reusable `Parser`, plus a `parse()` that reports whether the resulting tree has syntax errors (used for `parse_error` handling, never to abort).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_parser.py
from livegraph.static.parser import parse_source, has_errors


def test_parse_valid_source_has_no_errors():
    tree = parse_source(b"def f():\n    return 1\n")
    assert has_errors(tree) is False
    assert tree.root_node.type == "module"


def test_parse_broken_source_reports_errors():
    tree = parse_source(b"def f(:\n")
    assert has_errors(tree) is True


def test_parse_never_raises_on_garbage():
    tree = parse_source(b"\x00\x01 not python !!!")
    assert tree.root_node is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.static.parser'`.

- [ ] **Step 3: Write `livegraph/static/parser.py`**

```python
"""tree-sitter parsing for Python source."""
from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Tree

PY_LANGUAGE = Language(tspython.language())
_PARSER = Parser(PY_LANGUAGE)


def parse_source(source: bytes) -> Tree:
    """Parse Python ``source`` bytes into a tree-sitter ``Tree``.

    Never raises on malformed input — tree-sitter produces a tree with
    ERROR nodes instead. Use ``has_errors`` to detect that.
    """
    return _PARSER.parse(source)


def has_errors(tree: Tree) -> bool:
    """Return True if the parse tree contains syntax errors."""
    return tree.root_node.has_error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_parser.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/static/parser.py tests/unit/test_parser.py
git commit -m "feat: tree-sitter parser wrapper"
```

---

## Task 9: AST extraction (`queries/python.scm` + `static/extractor.py`)

**Files:**
- Create: `livegraph/queries/python.scm`
- Create: `livegraph/static/extractor.py`
- Test: `tests/unit/test_extractor.py`

`extract(rel_path, source)` returns `(definitions, imports, raw_calls)`. Definitions cover module-level functions, classes, and methods (nested one level into a class). `raw_calls` are unresolved `(caller_qn, callee_name, line)` triples — resolution happens in Task 10. The extractor walks the AST directly (the tree-sitter query in `python.scm` documents the node shapes; direct walking is used because call/definition nesting needs parent context).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_extractor.py
from livegraph.static.extractor import extract

SRC = b'''\
import os
from pkg.sub import helper


def top_level(x):
    helper(x)
    return x


class Handler:
    @property
    def name(self):
        return "h"

    def run(self):
        return top_level(1)
'''


def test_extracts_module_function():
    defs, _imports, _calls = extract("a.py", SRC)
    fn = [d for d in defs if d.kind == "function"]
    assert [d.qualified_name for d in fn] == ["a.py::top_level"]
    assert fn[0].start_line == 5


def test_extracts_class_and_methods():
    defs, _imports, _calls = extract("a.py", SRC)
    classes = [d for d in defs if d.kind == "class"]
    methods = [d for d in defs if d.kind == "method"]
    assert [d.qualified_name for d in classes] == ["a.py::Handler"]
    assert {d.qualified_name for d in methods} == {
        "a.py::Handler.name", "a.py::Handler.run",
    }
    assert all(m.parent_class == "a.py::Handler" for m in methods)


def test_extracts_decorators():
    defs, _imports, _calls = extract("a.py", SRC)
    name = next(d for d in defs if d.qualified_name == "a.py::Handler.name")
    assert name.decorators == ("property",)


def test_extracts_imports():
    _defs, imports, _calls = extract("a.py", SRC)
    assert {i.module for i in imports} == {"os", "pkg.sub"}


def test_extracts_raw_calls_with_caller_scope():
    _defs, _imports, calls = extract("a.py", SRC)
    pairs = {(c.caller_qn, c.callee_name) for c in calls}
    assert ("a.py::top_level", "helper") in pairs
    assert ("a.py::Handler.run", "top_level") in pairs


def test_broken_file_yields_empty_results():
    defs, imports, calls = extract("bad.py", b"def f(:\n")
    assert defs == [] and imports == [] and calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.static.extractor'`.

- [ ] **Step 3: Create `livegraph/queries/python.scm`**

```scheme
; Reference query documenting the Python node shapes livegraph extracts.
; The extractor walks the tree directly (it needs parent context), but
; these patterns describe the captured constructs.

(function_definition name: (identifier) @function.name) @function.def
(class_definition name: (identifier) @class.name) @class.def
(import_statement) @import
(import_from_statement) @import.from
(call function: (_) @call.callee) @call
```

- [ ] **Step 4: Write `livegraph/static/extractor.py`**

```python
"""Extract definitions, imports, and raw calls from a Python AST."""
from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from livegraph.models import Definition, ImportRecord
from livegraph.qualnames import symbol_qid
from livegraph.static.parser import has_errors, parse_source


@dataclass(frozen=True, slots=True)
class RawCall:
    """An unresolved call site: caller is known, callee is just a name."""

    caller_qn: str
    callee_name: str   # the simple/dotted name as written at the call site
    line: int


def extract(
    rel_path: str, source: bytes,
) -> tuple[list[Definition], list[ImportRecord], list[RawCall]]:
    """Extract definitions, imports, and raw calls from one Python file.

    A file with syntax errors yields three empty lists — the caller is
    responsible for still recording the File node with parse_error=True.
    """
    tree = parse_source(source)
    if has_errors(tree):
        return [], [], []

    definitions: list[Definition] = []
    imports: list[ImportRecord] = []
    calls: list[RawCall] = []
    _walk(tree.root_node, source, rel_path, _scope=None, _class=None,
          definitions=definitions, imports=imports, calls=calls)
    return definitions, imports, calls


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _walk(  # noqa: PLR0913 - a focused recursive visitor
    node: Node, source: bytes, rel_path: str,
    _scope: str | None, _class: str | None,
    definitions: list[Definition], imports: list[ImportRecord],
    calls: list[RawCall],
) -> None:
    """Depth-first visitor. ``_scope`` is the enclosing definition's
    qualified_name; ``_class`` is the enclosing class's qualified_name."""
    for child in node.children:
        if child.type == "function_definition":
            _handle_function(child, source, rel_path, _scope, _class,
                             definitions, imports, calls)
        elif child.type == "class_definition":
            _handle_class(child, source, rel_path, _scope,
                          definitions, imports, calls)
        elif child.type in ("import_statement", "import_from_statement"):
            imports.extend(_handle_import(child, source, rel_path))
        elif child.type == "call" and _scope is not None:
            calls.append(_handle_call(child, source, _scope))
            _walk(child, source, rel_path, _scope, _class,
                  definitions, imports, calls)
        else:
            _walk(child, source, rel_path, _scope, _class,
                  definitions, imports, calls)


def _name_of(node: Node, source: bytes) -> str:
    field = node.child_by_field_name("name")
    return _text(field, source) if field is not None else "<anonymous>"


def _decorators(node: Node, source: bytes) -> tuple[str, ...]:
    """Decorator identifiers, if ``node`` sits inside a decorated_definition."""
    parent = node.parent
    if parent is None or parent.type != "decorated_definition":
        return ()
    out: list[str] = []
    for child in parent.children:
        if child.type == "decorator":
            text = _text(child, source).lstrip("@").strip()
            out.append(text.split("(", 1)[0])
    return tuple(out)


def _handle_function(  # noqa: PLR0913
    node: Node, source: bytes, rel_path: str,
    scope: str | None, cls: str | None,
    definitions: list[Definition], imports: list[ImportRecord],
    calls: list[RawCall],
) -> None:
    name = _name_of(node, source)
    dotted = f"{_class_simple(cls)}.{name}" if cls is not None else name
    qn = symbol_qid(rel_path, dotted)
    kind = "method" if cls is not None else "function"
    definitions.append(Definition(
        qualified_name=qn, name=name, kind=kind, file=rel_path,
        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        decorators=_decorators(node, source), source=_text(node, source),
        parent_class=cls,
    ))
    body = node.child_by_field_name("body")
    if body is not None:
        # Nested defs descend with this function as scope but no class.
        _walk(body, source, rel_path, _scope=qn, _class=None,
              definitions=definitions, imports=imports, calls=calls)


def _handle_class(  # noqa: PLR0913
    node: Node, source: bytes, rel_path: str, scope: str | None,
    definitions: list[Definition], imports: list[ImportRecord],
    calls: list[RawCall],
) -> None:
    name = _name_of(node, source)
    qn = symbol_qid(rel_path, name)
    definitions.append(Definition(
        qualified_name=qn, name=name, kind="class", file=rel_path,
        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        decorators=_decorators(node, source), source=_text(node, source),
        parent_class=None,
    ))
    body = node.child_by_field_name("body")
    if body is not None:
        _walk(body, source, rel_path, _scope=qn, _class=qn,
              definitions=definitions, imports=imports, calls=calls)


def _class_simple(class_qn: str) -> str:
    """Return the bare class name from its qualified_name."""
    return class_qn.split("::", 1)[1]


def _handle_import(
    node: Node, source: bytes, rel_path: str,
) -> list[ImportRecord]:
    raw = _text(node, source)
    line = node.start_point[0] + 1
    modules: list[str] = []
    if node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                modules.append(_text(child, source))
            elif child.type == "aliased_import":
                inner = child.child_by_field_name("name")
                if inner is not None:
                    modules.append(_text(inner, source))
    else:  # import_from_statement
        mod = node.child_by_field_name("module_name")
        if mod is not None:
            modules.append(_text(mod, source))
    return [ImportRecord(file=rel_path, raw=raw, line=line, module=m)
            for m in modules]


def _handle_call(node: Node, source: bytes, scope: str) -> RawCall:
    callee = node.child_by_field_name("function")
    name = _text(callee, source) if callee is not None else "<dynamic>"
    return RawCall(caller_qn=scope, callee_name=name,
                   line=node.start_point[0] + 1)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_extractor.py -v`
Expected: PASS (6 passed). If a capture/field name mismatches the installed grammar, adjust against `tree-sitter-python`'s node types until green — do not weaken the assertions.

- [ ] **Step 6: Commit**

```bash
cd ~/livegraph
git add livegraph/queries/python.scm livegraph/static/extractor.py tests/unit/test_extractor.py
git commit -m "feat: AST extraction of definitions, imports, and raw calls"
```

---

## Task 10: Import & call resolution (`static/resolver.py`)

**Files:**
- Create: `livegraph/static/resolver.py`
- Test: `tests/unit/test_resolver.py`

`resolver.py` turns raw data into edges. `resolve_imports` classifies each import as an internal `File`, a `stdlib` module, or a `thirdparty` module. `resolve_calls` resolves each `RawCall` against the set of project-defined qualified_names — by simple-name match within the same file first, then anywhere in the project. Unresolved calls are dropped (runtime fills them).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_resolver.py
from livegraph.models import ImportRecord
from livegraph.static.extractor import RawCall
from livegraph.static.resolver import (
    resolve_imports, resolve_calls, ResolvedImport,
)


def test_resolve_internal_import_to_file():
    project_modules = {"pkg.sub": "pkg/sub.py", "pkg": "pkg/__init__.py"}
    imports = [ImportRecord("a.py", "from pkg.sub import x", 1, "pkg.sub")]
    resolved = resolve_imports(imports, project_modules)
    assert resolved == [
        ResolvedImport(file="a.py", target="pkg/sub.py", target_kind="file",
                       raw="from pkg.sub import x", line=1)
    ]


def test_resolve_stdlib_import():
    resolved = resolve_imports(
        [ImportRecord("a.py", "import os", 1, "os")], {})
    assert resolved[0].target == "os"
    assert resolved[0].target_kind == "stdlib"


def test_resolve_thirdparty_import():
    resolved = resolve_imports(
        [ImportRecord("a.py", "import numpy", 1, "numpy")], {})
    assert resolved[0].target == "numpy"
    assert resolved[0].target_kind == "thirdparty"


def test_resolve_calls_prefers_same_file():
    defined = {"a.py::f", "b.py::f"}
    calls = [RawCall(caller_qn="a.py::g", callee_name="f", line=2)]
    edges = resolve_calls(calls, defined)
    assert len(edges) == 1
    assert edges[0].caller_qn == "a.py::g"
    assert edges[0].callee_qn == "a.py::f"
    assert edges[0].static is True


def test_resolve_calls_drops_unresolved():
    edges = resolve_calls(
        [RawCall("a.py::g", "print", 1)], defined={"a.py::g"})
    assert edges == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.static.resolver'`.

- [ ] **Step 3: Write `livegraph/static/resolver.py`**

```python
"""Resolve raw imports and calls into graph edges (best-effort, static)."""
from __future__ import annotations

import sys
from dataclasses import dataclass

from livegraph.models import CallEdge, ImportRecord
from livegraph.static.extractor import RawCall


@dataclass(frozen=True, slots=True)
class ResolvedImport:
    """An import resolved to a concrete target."""

    file: str
    target: str          # a File path, or a module name
    target_kind: str     # "file" | "stdlib" | "thirdparty"
    raw: str
    line: int


def resolve_imports(
    imports: list[ImportRecord], project_modules: dict[str, str],
) -> list[ResolvedImport]:
    """Classify each import.

    ``project_modules`` maps a dotted module name to its File path for
    every module inside the project.
    """
    out: list[ResolvedImport] = []
    for imp in imports:
        if imp.module in project_modules:
            out.append(ResolvedImport(imp.file, project_modules[imp.module],
                                      "file", imp.raw, imp.line))
        else:
            top = imp.module.split(".", 1)[0]
            kind = "stdlib" if top in sys.stdlib_module_names else "thirdparty"
            out.append(ResolvedImport(imp.file, imp.module, kind,
                                      imp.raw, imp.line))
    return out


def resolve_calls(
    calls: list[RawCall], defined: set[str],
) -> list[CallEdge]:
    """Resolve raw calls to CALLS edges against project-defined symbols.

    A call's callee name is matched by simple name: first against a
    definition in the caller's own file, then anywhere in the project.
    Unresolved calls (stdlib, third-party, dynamic) are dropped.
    """
    # Index defined qualified_names by their simple name.
    by_simple: dict[str, list[str]] = {}
    for qn in defined:
        simple = qn.split("::", 1)[1].split(".")[-1]
        by_simple.setdefault(simple, []).append(qn)

    edges: dict[tuple[str, str], CallEdge] = {}
    for call in calls:
        simple = call.callee_name.split(".")[-1]
        candidates = by_simple.get(simple)
        if not candidates:
            continue
        caller_file = call.caller_qn.split("::", 1)[0]
        same_file = [c for c in candidates if c.split("::", 1)[0] == caller_file]
        callee_qn = same_file[0] if same_file else candidates[0]
        key = (call.caller_qn, callee_qn)
        if key not in edges:
            edges[key] = CallEdge(caller_qn=call.caller_qn,
                                  callee_qn=callee_qn, static=True)
    return list(edges.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_resolver.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/static/resolver.py tests/unit/test_resolver.py
git commit -m "feat: best-effort static import and call resolution"
```

---

## Task 11: Phase 1 orchestrator (`ingest.py`)

**Files:**
- Create: `livegraph/discovery.py`
- Create: `livegraph/ingest.py`
- Test: `tests/unit/test_discovery.py`
- Test: `tests/unit/test_ingest.py`

`discovery.py` walks a directory for `.py` files, skipping junk directories. `ingest.py` ties Phase 1 together: discover → parse/extract → resolve → write. It writes through `GraphWriter` and a `Module`-writing step (added here because modules first appear in Phase 1).

- [ ] **Step 1: Write the failing discovery test**

```python
# tests/unit/test_discovery.py
from livegraph.discovery import discover_python_files


def test_discovers_py_files_relative(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1")
    (tmp_path / "b.py").write_text("y = 2")
    found = set(discover_python_files(str(tmp_path)))
    assert found == {"b.py", "pkg/a.py"}


def test_skips_junk_directories(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("z = 3")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "c.py").write_text("c = 4")
    (tmp_path / "real.py").write_text("r = 5")
    assert set(discover_python_files(str(tmp_path))) == {"real.py"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.discovery'`.

- [ ] **Step 3: Write `livegraph/discovery.py`**

```python
"""Discover Python source files under a project root."""
from __future__ import annotations

import os
from collections.abc import Iterator

_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env", ".tox", "build", "dist",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "node_modules",
}


def discover_python_files(root: str) -> Iterator[str]:
    """Yield project-relative, forward-slash paths of every ``.py`` file."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".py"):
                abs_path = os.path.join(dirpath, filename)
                yield os.path.relpath(abs_path, root).replace("\\", "/")
```

- [ ] **Step 4: Run discovery test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_discovery.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Write the failing ingest test**

```python
# tests/unit/test_ingest.py
from livegraph.graph.backend import FakeBackend
from livegraph.ingest import ingest_project


def test_ingest_writes_files_definitions_and_calls(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def total(xs):\n    return add(xs[0], xs[1])\n"
    )
    backend = FakeBackend()
    summary = ingest_project(str(tmp_path), backend, project_name="demo",
                             batch_size=100)
    assert summary.files == 1
    assert summary.definitions == 2
    assert summary.call_edges == 1     # total -> add
    assert summary.parse_errors == 0
    issued = " ".join(q for q, _ in backend.calls)
    assert "CONSTRAINT" in issued and ":Function" in issued and ":CALLS" in issued


def test_ingest_records_parse_errors_without_aborting(tmp_path):
    (tmp_path / "ok.py").write_text("def f():\n    return 1\n")
    (tmp_path / "bad.py").write_text("def f(:\n")
    backend = FakeBackend()
    summary = ingest_project(str(tmp_path), backend, project_name="demo",
                             batch_size=100)
    assert summary.files == 2
    assert summary.parse_errors == 1
    assert summary.definitions == 1
```

- [ ] **Step 6: Run ingest test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.ingest'`.

- [ ] **Step 7: Write `livegraph/ingest.py`**

```python
"""Phase 1: build the static graph from a Python codebase."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from livegraph.discovery import discover_python_files
from livegraph.graph.backend import GraphBackend
from livegraph.graph.schema import create_schema
from livegraph.graph.writer import GraphWriter
from livegraph.models import FileRecord
from livegraph.static.extractor import extract
from livegraph.static.parser import has_errors, parse_source
from livegraph.static.resolver import (
    ResolvedImport, resolve_calls, resolve_imports,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestSummary:
    """Counts produced by a Phase 1 run."""

    files: int
    definitions: int
    call_edges: int
    parse_errors: int


def _module_name(rel_path: str) -> str:
    """Dotted module name for a project-relative file path."""
    no_ext = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    parts = no_ext.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def ingest_project(
    root: str, backend: GraphBackend, project_name: str | None = None,
    batch_size: int = 1000,
) -> IngestSummary:
    """Run Phase 1: discover, parse, resolve, and write the static graph."""
    project_name = project_name or os.path.basename(os.path.abspath(root))
    backend.verify()
    create_schema(backend)
    writer = GraphWriter(backend, batch_size=batch_size)

    rel_paths = sorted(discover_python_files(root))
    project_modules = {_module_name(p): p for p in rel_paths}

    file_records: list[FileRecord] = []
    all_defs = []
    all_imports = []
    all_raw_calls = []
    parse_errors = 0

    for rel in rel_paths:
        with open(os.path.join(root, rel), "rb") as handle:
            source = handle.read()
        broken = has_errors(parse_source(source))
        file_records.append(FileRecord(
            path=rel, name=os.path.basename(rel), parse_error=broken))
        if broken:
            parse_errors += 1
            logger.warning("skipping unparseable file: %s", rel)
            continue
        defs, imports, raw_calls = extract(rel, source)
        all_defs.extend(defs)
        all_imports.extend(imports)
        all_raw_calls.extend(raw_calls)

    defined = {d.qualified_name for d in all_defs}
    call_edges = resolve_calls(all_raw_calls, defined)
    resolved_imports = resolve_imports(all_imports, project_modules)

    writer.write_files(project_name, file_records)
    writer.write_definitions(all_defs)
    _write_imports(backend, resolved_imports, batch_size)
    writer.write_calls(call_edges)

    return IngestSummary(
        files=len(file_records), definitions=len(all_defs),
        call_edges=len(call_edges), parse_errors=parse_errors,
    )


def _write_imports(
    backend: GraphBackend, imports: list[ResolvedImport], batch_size: int,
) -> None:
    """MERGE IMPORTS edges to File or Module targets, batched."""
    files = [i for i in imports if i.target_kind == "file"]
    modules = [i for i in imports if i.target_kind != "file"]
    for start in range(0, len(files), batch_size):
        rows = [{"file": i.file, "target": i.target, "raw": i.raw,
                 "line": i.line} for i in files[start:start + batch_size]]
        backend.execute(
            "UNWIND $rows AS row "
            "MATCH (src:File {path: row.file}) "
            "MATCH (dst:File {path: row.target}) "
            "MERGE (src)-[r:IMPORTS]->(dst) "
            "SET r.raw = row.raw, r.line = row.line",
            rows=rows,
        )
    for start in range(0, len(modules), batch_size):
        rows = [{"file": i.file, "target": i.target, "kind": i.target_kind,
                 "raw": i.raw, "line": i.line}
                for i in modules[start:start + batch_size]]
        backend.execute(
            "UNWIND $rows AS row "
            "MATCH (src:File {path: row.file}) "
            "MERGE (m:Module {name: row.target}) SET m.kind = row.kind "
            "MERGE (src)-[r:IMPORTS]->(m) "
            "SET r.raw = row.raw, r.line = row.line",
            rows=rows,
        )
```

- [ ] **Step 8: Run ingest test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_ingest.py -v`
Expected: PASS (2 passed).

- [ ] **Step 9: Commit**

```bash
cd ~/livegraph
git add livegraph/discovery.py livegraph/ingest.py tests/unit/test_discovery.py tests/unit/test_ingest.py
git commit -m "feat: Phase 1 ingestion orchestrator"
```

---

## Task 12: CLI — `ingest`, `clean`, `status` (`cli.py`)

**Files:**
- Create: `livegraph/cli.py`
- Test: `tests/unit/test_cli.py`

The CLI uses Typer. `ingest`, `clean`, and `status` are added now; `trace` and `build` are added in Task 19. A `_make_backend` helper builds a `Neo4jBackend` from settings and is monkeypatched in tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli.py
from typer.testing import CliRunner

import livegraph.cli as cli
from livegraph.graph.backend import FakeBackend

runner = CliRunner()


def test_ingest_command_invokes_ingestion(tmp_path, monkeypatch):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    result = runner.invoke(cli.app, ["ingest", str(tmp_path)])
    assert result.exit_code == 0
    assert "files" in result.stdout.lower()


def test_status_command_reports_counts(monkeypatch):
    backend = FakeBackend(rows=[{"label": "File", "n": 7}])
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "File" in result.stdout


def test_clean_command_runs_detach_delete(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    result = runner.invoke(cli.app, ["clean", "--yes"])
    assert result.exit_code == 0
    assert any("DETACH DELETE" in q for q, _ in backend.calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.cli'`.

- [ ] **Step 3: Write `livegraph/cli.py`**

```python
"""livegraph command-line interface."""
from __future__ import annotations

import logging

import typer

from livegraph.config import load_settings
from livegraph.graph.backend import GraphBackend, Neo4jBackend
from livegraph.ingest import ingest_project

app = typer.Typer(help="A runtime-augmented code knowledge graph for Python.")


def _make_backend() -> GraphBackend:
    """Build a graph backend from configuration (patched in tests)."""
    settings = load_settings()
    logging.basicConfig(level=settings.livegraph_log_level)
    return Neo4jBackend(settings.neo4j_uri, settings.neo4j_user,
                        settings.neo4j_password)


@app.command()
def ingest(path: str = typer.Argument(..., help="Project root to ingest")) -> None:
    """Phase 1: build the static graph for the project at PATH."""
    settings = load_settings()
    backend = _make_backend()
    try:
        summary = ingest_project(path, backend,
                                 batch_size=settings.livegraph_batch_size)
    finally:
        backend.close()
    typer.echo(
        f"Phase 1 complete: {summary.files} files, "
        f"{summary.definitions} definitions, {summary.call_edges} static "
        f"call edges, {summary.parse_errors} parse errors."
    )


@app.command()
def status() -> None:
    """Show node counts in the graph."""
    backend = _make_backend()
    try:
        rows = backend.execute(
            "MATCH (n) UNWIND labels(n) AS label "
            "RETURN label, count(*) AS n ORDER BY label"
        )
    finally:
        backend.close()
    if not rows:
        typer.echo("Graph is empty.")
        return
    for row in rows:
        typer.echo(f"{row['label']}: {row['n']}")


@app.command()
def clean(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation"),
) -> None:
    """Delete every node and relationship in the graph."""
    if not yes:
        typer.confirm("Delete the entire graph?", abort=True)
    backend = _make_backend()
    try:
        backend.execute("MATCH (n) DETACH DELETE n")
    finally:
        backend.close()
    typer.echo("Graph cleared.")


if __name__ == "__main__":  # pragma: no cover
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_cli.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/cli.py tests/unit/test_cli.py
git commit -m "feat: CLI with ingest, status, and clean commands"
```

---

## Task 13: Sample fixture project + Phase 1 integration test

**Files:**
- Create: `tests/fixtures/sample_project/calculator.py`
- Create: `tests/fixtures/sample_project/runner.py`
- Create: `tests/fixtures/sample_project/test_calculator.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_phase1_integration.py`

The fixture is a tiny project used by both Phase 1 and Phase 2 integration tests. `runner.py` contains a **deliberate dynamic-dispatch call** (`op(a, b)`) that static analysis cannot resolve — the basis of the Task 20 differentiator test.

- [ ] **Step 1: Create `tests/fixtures/sample_project/calculator.py`**

```python
"""Sample module: a tiny calculator."""


class Calculator:
    """Adds and multiplies numbers."""

    def add(self, a, b):
        return a + b

    def multiply(self, a, b):
        return a * b
```

- [ ] **Step 2: Create `tests/fixtures/sample_project/runner.py`**

```python
"""Sample module exercising dynamic dispatch.

``run_operation`` calls ``op`` without static analysis being able to
know that ``op`` is ``Calculator.add`` — only running it reveals that.
"""
from calculator import Calculator


def run_operation(op, a, b):
    return op(a, b)


def main():
    calc = Calculator()
    return run_operation(calc.add, 3, 4)
```

- [ ] **Step 3: Create `tests/fixtures/sample_project/test_calculator.py`**

```python
"""Sample test suite for the fixture project."""
from calculator import Calculator
from runner import main, run_operation


def test_add():
    assert Calculator().add(2, 3) == 5


def test_run_operation_dynamic_dispatch():
    assert run_operation(Calculator().multiply, 2, 5) == 10


def test_main():
    assert main() == 7
```

- [ ] **Step 4: Create `tests/integration/conftest.py`**

```python
"""Shared fixtures for integration tests (require a running Neo4j)."""
from __future__ import annotations

import os

import pytest

from livegraph.config import load_settings
from livegraph.graph.backend import Neo4jBackend

SAMPLE_PROJECT = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "sample_project")


@pytest.fixture()
def neo4j_backend():
    """Yield a Neo4jBackend, wiping the graph before and after the test."""
    settings = load_settings()
    backend = Neo4jBackend(settings.neo4j_uri, settings.neo4j_user,
                           settings.neo4j_password)
    try:
        backend.verify()
    except ConnectionError:
        pytest.skip("Neo4j not reachable; run `docker compose up -d`.")
    backend.execute("MATCH (n) DETACH DELETE n")
    yield backend
    backend.execute("MATCH (n) DETACH DELETE n")
    backend.close()


@pytest.fixture()
def sample_project_path() -> str:
    """Absolute path to the fixture sample project."""
    return os.path.abspath(SAMPLE_PROJECT)
```

- [ ] **Step 5: Write `tests/integration/test_phase1_integration.py`**

```python
"""Phase 1 end-to-end against a real Neo4j."""
import pytest

from livegraph.ingest import ingest_project

pytestmark = pytest.mark.integration


def test_ingest_sample_project_writes_expected_nodes(
    neo4j_backend, sample_project_path,
):
    summary = ingest_project(sample_project_path, neo4j_backend,
                             project_name="sample", batch_size=100)
    # calculator.py, runner.py, test_calculator.py
    assert summary.files == 3
    assert summary.parse_errors == 0

    rows = neo4j_backend.execute(
        "MATCH (m:Method {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN m.name AS name"
    )
    assert rows == [{"name": "add"}]

    classes = neo4j_backend.execute(
        "MATCH (:Class {qualified_name: 'calculator.py::Calculator'})"
        "-[:HAS_METHOD]->(m:Method) RETURN count(m) AS n"
    )
    assert classes[0]["n"] == 2


def test_ingest_is_idempotent(neo4j_backend, sample_project_path):
    for _ in range(2):
        ingest_project(sample_project_path, neo4j_backend,
                       project_name="sample", batch_size=100)
    rows = neo4j_backend.execute("MATCH (f:File) RETURN count(f) AS n")
    assert rows[0]["n"] == 3   # not 6 — MERGE de-duplicates
```

- [ ] **Step 6: Run the integration test**

Run:
```bash
cd ~/livegraph && docker compose up -d && sleep 20
.venv/bin/pytest tests/integration/test_phase1_integration.py -v -m integration
```
Expected: PASS (2 passed). If Neo4j is still starting, wait and re-run.

- [ ] **Step 7: Run the full unit suite to confirm nothing regressed**

Run: `cd ~/livegraph && .venv/bin/pytest -m "not integration" -q`
Expected: all unit tests PASS.

- [ ] **Step 8: Commit**

```bash
cd ~/livegraph
git add tests/fixtures tests/integration
git commit -m "test: sample fixture project and Phase 1 integration tests"
```

---

## Task 14: Runtime models + qualname mapping from code objects

**Files:**
- Modify: `livegraph/models.py`
- Create: `livegraph/runtime/observations.py`
- Test: `tests/unit/test_observations.py`

This adds the Phase 2 record types and `qid_from_code()`, which maps a live CPython `CodeType` to the same `qualified_name` Phase 1 produced. `qid_from_code` returns `None` for code outside the project root or code it cannot map (logged by callers, never crashed on).

- [ ] **Step 1: Append runtime models to `livegraph/models.py`**

Add at the end of `livegraph/models.py`:

```python
@dataclass(frozen=True, slots=True)
class RuntimeCall:
    """A caller->callee call observed during a traced test run."""

    caller_qn: str
    callee_qn: str
    test_qn: str
    call_site_line: int


@dataclass(frozen=True, slots=True)
class TestResult:
    """The outcome of a single executed test."""

    qualified_name: str
    outcome: str         # "passed" | "failed" | "skipped"
    duration: float


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    """Per-test coverage of one definition."""

    test_qn: str
    symbol_qn: str
    lines_covered: int
    lines_total: int

    @property
    def coverage_pct(self) -> float:
        if self.lines_total == 0:
            return 0.0
        return round(100.0 * self.lines_covered / self.lines_total, 2)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_observations.py
import types

from livegraph.runtime.observations import qid_from_code


def _code_of(func) -> types.CodeType:
    return func.__code__


def test_qid_for_module_function(tmp_path):
    src = "def target():\n    return 1\n"
    path = tmp_path / "m.py"
    path.write_text(src)
    namespace: dict = {}
    exec(compile(src, str(path), "exec"), namespace)  # noqa: S102
    qid = qid_from_code(_code_of(namespace["target"]), str(tmp_path))
    assert qid == "m.py::target"


def test_qid_for_method(tmp_path):
    src = "class C:\n    def run(self):\n        return 2\n"
    path = tmp_path / "m.py"
    path.write_text(src)
    namespace: dict = {}
    exec(compile(src, str(path), "exec"), namespace)  # noqa: S102
    qid = qid_from_code(_code_of(namespace["C"].run), str(tmp_path))
    assert qid == "m.py::C.run"


def test_qid_outside_root_is_none(tmp_path):
    src = "def f():\n    return 1\n"
    namespace: dict = {}
    exec(compile(src, "/elsewhere/x.py", "exec"), namespace)  # noqa: S102
    assert qid_from_code(_code_of(namespace["f"]), str(tmp_path)) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_observations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.runtime.observations'`.

- [ ] **Step 4: Write `livegraph/runtime/observations.py`**

```python
"""Map live CPython code objects to livegraph qualified_names."""
from __future__ import annotations

import types

from livegraph.qualnames import normalize_co_qualname, rel_path, symbol_qid


def qid_from_code(code: types.CodeType, root: str) -> str | None:
    """Return the qualified_name for a code object, or None.

    None means the code is outside ``root`` (stdlib, third-party) or
    otherwise cannot be mapped — callers log and skip such frames.
    """
    rel = rel_path(code.co_filename, root)
    if rel is None:
        return None
    dotted = normalize_co_qualname(code.co_qualname)
    if not dotted or "<" in dotted:
        # Module bodies, comprehensions, lambdas — not v1 mappable.
        return None
    return symbol_qid(rel, dotted)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_observations.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Run model tests to confirm no regression**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_models.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd ~/livegraph
git add livegraph/models.py livegraph/runtime/observations.py tests/unit/test_observations.py
git commit -m "feat: runtime models and code-object qualname mapping"
```

---

## Task 15: Call tracer (`runtime/tracer.py`)

**Files:**
- Create: `livegraph/runtime/tracer.py`
- Test: `tests/unit/test_tracer.py`

`CallTracer` registers a `sys.monitoring` tool under a dedicated tool ID, listens for `CALL` events, and records caller→callee edges scoped to the currently-running test. The `CALL` callback receives the caller's code object and the callee object; both are mapped via `qid_from_code`. Calls where either side is outside the project are ignored.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_tracer.py
import os

from livegraph.runtime.tracer import CallTracer


def test_tracer_records_project_calls(tmp_path):
    src = (
        "def callee():\n    return 1\n\n"
        "def caller():\n    return callee()\n"
    )
    path = tmp_path / "m.py"
    path.write_text(src)
    namespace: dict = {}
    exec(compile(src, str(path), "exec"), namespace)  # noqa: S102

    tracer = CallTracer(root=str(tmp_path), tool_id=3)
    tracer.start()
    tracer.set_current_test("m.py::test_it")
    try:
        namespace["caller"]()
    finally:
        tracer.stop()

    calls = tracer.runtime_calls()
    pairs = {(c.caller_qn, c.callee_qn) for c in calls}
    assert ("m.py::caller", "m.py::callee") in pairs
    assert all(c.test_qn == "m.py::test_it" for c in calls)


def test_tracer_ignores_calls_outside_project(tmp_path):
    src = "def caller():\n    return len([1, 2, 3])\n"
    path = tmp_path / "m.py"
    path.write_text(src)
    namespace: dict = {}
    exec(compile(src, str(path), "exec"), namespace)  # noqa: S102

    tracer = CallTracer(root=str(tmp_path), tool_id=3)
    tracer.start()
    tracer.set_current_test("m.py::t")
    try:
        namespace["caller"]()
    finally:
        tracer.stop()
    # len() is a builtin -> no project edge recorded.
    assert tracer.runtime_calls() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_tracer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.runtime.tracer'`.

- [ ] **Step 3: Write `livegraph/runtime/tracer.py`**

```python
"""Capture real call edges via sys.monitoring (PEP 669)."""
from __future__ import annotations

import sys
import types
from collections import Counter

from livegraph.models import RuntimeCall
from livegraph.runtime.observations import qid_from_code


class CallTracer:
    """Records caller->callee edges during a traced run.

    Uses a dedicated ``sys.monitoring`` tool id and listens for ``CALL``
    events. Each edge is attributed to the test currently set via
    ``set_current_test``. Only calls where both sides resolve to a
    project qualified_name are recorded.
    """

    def __init__(self, root: str, tool_id: int = 3) -> None:
        self._root = root
        self._tool_id = tool_id
        self._current_test: str | None = None
        # (caller_qn, callee_qn, test_qn) -> observed count
        self._counts: Counter[tuple[str, str, str]] = Counter()

    def set_current_test(self, test_qn: str | None) -> None:
        """Attribute subsequently observed calls to ``test_qn``."""
        self._current_test = test_qn

    def start(self) -> None:
        """Register the monitoring tool and begin listening for calls."""
        mon = sys.monitoring
        mon.use_tool_id(self._tool_id, "livegraph")
        mon.register_callback(self._tool_id, mon.events.CALL, self._on_call)
        mon.set_events(self._tool_id, mon.events.CALL)

    def stop(self) -> None:
        """Stop listening and release the monitoring tool id."""
        mon = sys.monitoring
        mon.set_events(self._tool_id, 0)
        mon.register_callback(self._tool_id, mon.events.CALL, None)
        mon.free_tool_id(self._tool_id)

    def runtime_calls(self) -> list[RuntimeCall]:
        """Return the distinct observed call edges with counts."""
        out: list[RuntimeCall] = []
        for (caller, callee, test_qn), count in self._counts.items():
            out.append(RuntimeCall(caller_qn=caller, callee_qn=callee,
                                   test_qn=test_qn, call_site_line=0))
            # call_site_line is 0 in v1; counts are kept separately.
        return out

    def counts(self) -> dict[tuple[str, str, str], int]:
        """Expose raw observation counts (used by the merge step)."""
        return dict(self._counts)

    # -- monitoring callback ---------------------------------------------

    def _on_call(
        self, code: types.CodeType, instruction_offset: int,
        callable_obj: object, arg0: object,
    ) -> object:
        """sys.monitoring CALL callback: code is the caller frame."""
        if self._current_test is None:
            return None
        callee_code = _code_of(callable_obj)
        if callee_code is None:
            return None
        caller_qn = qid_from_code(code, self._root)
        callee_qn = qid_from_code(callee_code, self._root)
        if caller_qn is None or callee_qn is None:
            return None
        self._counts[(caller_qn, callee_qn, self._current_test)] += 1
        return None


def _code_of(obj: object) -> types.CodeType | None:
    """Best-effort extraction of a code object from a callable."""
    code = getattr(obj, "__code__", None)
    if isinstance(code, types.CodeType):
        return code
    # Bound/unbound methods wrap the function in __func__.
    func = getattr(obj, "__func__", None)
    if func is not None:
        inner = getattr(func, "__code__", None)
        if isinstance(inner, types.CodeType):
            return inner
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_tracer.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/runtime/tracer.py tests/unit/test_tracer.py
git commit -m "feat: sys.monitoring-based call tracer"
```

---

## Task 16: Coverage adapter (`runtime/coverage_adapter.py`)

**Files:**
- Create: `livegraph/runtime/coverage_adapter.py`
- Test: `tests/unit/test_coverage_adapter.py`

`map_coverage_to_symbols` takes per-test coverage (test → set of `(rel_path, line)`) plus the project's definitions and produces `CoverageRecord`s, attributing covered lines to the definition whose `[start_line, end_line]` span contains them.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_coverage_adapter.py
from livegraph.models import Definition
from livegraph.runtime.coverage_adapter import map_coverage_to_symbols

DEFS = [
    Definition("m.py::f", "f", "function", "m.py", 1, 3, (), "..."),
    Definition("m.py::g", "g", "function", "m.py", 5, 8, (), "..."),
]


def test_attributes_lines_to_containing_definition():
    per_test = {"m.py::test_a": {("m.py", 1), ("m.py", 2)}}
    records = map_coverage_to_symbols(per_test, DEFS)
    by_symbol = {r.symbol_qn: r for r in records}
    assert by_symbol["m.py::f"].lines_covered == 2
    assert by_symbol["m.py::f"].lines_total == 3
    assert by_symbol["m.py::f"].coverage_pct == 66.67


def test_lines_outside_any_definition_are_ignored():
    per_test = {"m.py::t": {("m.py", 99)}}
    assert map_coverage_to_symbols(per_test, DEFS) == []


def test_only_emits_records_for_covered_definitions():
    per_test = {"m.py::t": {("m.py", 6)}}
    records = map_coverage_to_symbols(per_test, DEFS)
    assert [r.symbol_qn for r in records] == ["m.py::g"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_coverage_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.runtime.coverage_adapter'`.

- [ ] **Step 3: Write `livegraph/runtime/coverage_adapter.py`**

```python
"""Attribute per-test coverage lines to definitions."""
from __future__ import annotations

from collections.abc import Iterable

from livegraph.models import CoverageRecord, Definition


def map_coverage_to_symbols(
    per_test_lines: dict[str, set[tuple[str, int]]],
    definitions: Iterable[Definition],
) -> list[CoverageRecord]:
    """Build CoverageRecords from per-test covered (file, line) pairs.

    ``per_test_lines`` maps a test qualified_name to the set of
    ``(rel_path, line_number)`` pairs executed during that test.
    A line is attributed to the definition whose line span contains it.
    Functions and methods are attributed; classes are skipped (their
    coverage is the union of their methods).
    """
    defs = [d for d in definitions if d.kind in ("function", "method")]
    records: list[CoverageRecord] = []

    for test_qn, lines in per_test_lines.items():
        for definition in defs:
            span = range(definition.start_line, definition.end_line + 1)
            total = len(span)
            covered = sum(
                1 for line in span
                if (definition.file, line) in lines
            )
            if covered > 0:
                records.append(CoverageRecord(
                    test_qn=test_qn, symbol_qn=definition.qualified_name,
                    lines_covered=covered, lines_total=total,
                ))
    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_coverage_adapter.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/runtime/coverage_adapter.py tests/unit/test_coverage_adapter.py
git commit -m "feat: coverage-to-symbol attribution"
```

---

## Task 17: pytest plugin (`runtime/pytest_plugin.py`)

**Files:**
- Create: `livegraph/runtime/pytest_plugin.py`
- Test: `tests/unit/test_pytest_plugin.py`

The plugin runs *inside the target's interpreter*. It starts a `CallTracer` and a `coverage.Coverage` (with `dynamic_context="test_function"`), marks the current test around each call, and on shutdown writes an observations JSON file. It reads two environment variables: `LIVEGRAPH_ROOT` (target project root) and `LIVEGRAPH_OUTPUT` (JSON path). The test exercises the plugin's class directly with a fake session rather than spawning pytest.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pytest_plugin.py
import json
import types

from livegraph.runtime.pytest_plugin import LivegraphPlugin


class _FakeItem:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid


def test_plugin_writes_observations_json(tmp_path):
    src = "def callee():\n    return 1\n\ndef caller():\n    return callee()\n"
    (tmp_path / "m.py").write_text(src)
    namespace: dict = {}
    exec(compile(src, str(tmp_path / "m.py"), "exec"), namespace)  # noqa: S102

    output = tmp_path / "obs.json"
    plugin = LivegraphPlugin(root=str(tmp_path), output_path=str(output),
                             tool_id=4, enable_coverage=False)
    plugin.start()
    item = _FakeItem("tests/m_test.py::test_caller")
    plugin.before_test(item)
    namespace["caller"]()
    plugin.after_test(item, outcome="passed", duration=0.01)
    plugin.finish()

    data = json.loads(output.read_text())
    assert any(c["caller_qn"] == "m.py::caller"
               and c["callee_qn"] == "m.py::callee"
               for c in data["runtime_calls"])
    assert data["tests"][0]["outcome"] == "passed"


def test_plugin_test_qn_uses_nodeid(tmp_path):
    plugin = LivegraphPlugin(root=str(tmp_path),
                             output_path=str(tmp_path / "o.json"),
                             tool_id=4, enable_coverage=False)
    assert plugin.test_qn(_FakeItem("tests/x.py::test_y")) == "tests/x.py::test_y"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_pytest_plugin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.runtime.pytest_plugin'`.

- [ ] **Step 3: Write `livegraph/runtime/pytest_plugin.py`**

```python
"""pytest plugin: trace the target suite and dump observations to JSON.

Runs inside the *target* project's interpreter. Configuration arrives via
two environment variables:

* ``LIVEGRAPH_ROOT``   - absolute path of the project being traced
* ``LIVEGRAPH_OUTPUT`` - path the observations JSON is written to

Only ``coverage`` must be importable in the target environment.
"""
from __future__ import annotations

import json
import os
from typing import Any

from livegraph.runtime.tracer import CallTracer

_TOOL_ID = 4


class LivegraphPlugin:
    """Drives tracing and coverage across a pytest session."""

    def __init__(self, root: str, output_path: str, tool_id: int = _TOOL_ID,
                 enable_coverage: bool = True) -> None:
        self._root = root
        self._output_path = output_path
        self._tracer = CallTracer(root=root, tool_id=tool_id)
        self._tests: list[dict[str, Any]] = []
        self._enable_coverage = enable_coverage
        self._coverage: Any = None

    @staticmethod
    def test_qn(item: Any) -> str:
        """Qualified name for a test = its pytest nodeid."""
        return str(item.nodeid)

    def start(self) -> None:
        """Begin tracing (and coverage, if enabled)."""
        if self._enable_coverage:
            import coverage

            self._coverage = coverage.Coverage(
                data_file=None, branch=False,
                config_file=False, source=[self._root],
            )
            self._coverage.start()
        self._tracer.start()

    def before_test(self, item: Any) -> None:
        """Mark the test subsequent observations belong to.

        The coverage measurement context is switched manually to the
        test's qualified name so coverage contexts line up exactly with
        the test node identities used elsewhere in the graph.
        """
        test_qn = self.test_qn(item)
        self._tracer.set_current_test(test_qn)
        if self._coverage is not None:
            self._coverage.switch_context(test_qn)

    def after_test(self, item: Any, outcome: str, duration: float) -> None:
        """Record a finished test's outcome."""
        self._tracer.set_current_test(None)
        self._tests.append({
            "qualified_name": self.test_qn(item),
            "outcome": outcome, "duration": duration,
        })

    def finish(self) -> None:
        """Stop tracing/coverage and write the observations JSON."""
        self._tracer.stop()
        coverage_payload = self._collect_coverage()
        observations = {
            "root": self._root,
            "runtime_calls": [
                {"caller_qn": caller, "callee_qn": callee,
                 "test_qn": test_qn, "observed_count": count}
                for (caller, callee, test_qn), count
                in self._tracer.counts().items()
            ],
            "tests": self._tests,
            "coverage": coverage_payload,
        }
        with open(self._output_path, "w", encoding="utf-8") as handle:
            json.dump(observations, handle, indent=2)

    def _collect_coverage(self) -> list[dict[str, Any]]:
        """Return per-test coverage as {test_qn, file, lines} dicts."""
        if self._coverage is None:
            return []
        self._coverage.stop()
        data = self._coverage.get_data()
        payload: list[dict[str, Any]] = []
        for measured_file in data.measured_files():
            rel = os.path.relpath(measured_file, self._root).replace("\\", "/")
            if rel.startswith(".."):
                continue
            contexts = data.contexts_by_lineno(measured_file)
            per_test: dict[str, list[int]] = {}
            for line, ctx_list in contexts.items():
                for ctx in ctx_list:
                    if ctx:
                        per_test.setdefault(ctx, []).append(line)
            for ctx, lines in per_test.items():
                payload.append({"test_context": ctx, "file": rel,
                                "lines": sorted(lines)})
        return payload


# -- pytest hook entry points -------------------------------------------
# pytest discovers these module-level hooks when the plugin is loaded
# with ``-p livegraph.runtime.pytest_plugin``.

_PLUGIN: LivegraphPlugin | None = None


def pytest_configure(config: Any) -> None:  # pragma: no cover - needs pytest
    global _PLUGIN
    root = os.environ.get("LIVEGRAPH_ROOT")
    output = os.environ.get("LIVEGRAPH_OUTPUT")
    if not root or not output:
        return
    _PLUGIN = LivegraphPlugin(root=root, output_path=output)
    _PLUGIN.start()


def pytest_runtest_call(item: Any) -> None:  # pragma: no cover - needs pytest
    if _PLUGIN is not None:
        _PLUGIN.before_test(item)


def pytest_runtest_logreport(report: Any) -> None:  # pragma: no cover
    if _PLUGIN is not None and report.when == "call":
        _PLUGIN.after_test(report, outcome=report.outcome,
                           duration=report.duration)


def pytest_unconfigure(config: Any) -> None:  # pragma: no cover - needs pytest
    if _PLUGIN is not None:
        _PLUGIN.finish()
```

Note: in `pytest_runtest_logreport` the `report` object carries `nodeid`, so `LivegraphPlugin.test_qn` works on it directly.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_pytest_plugin.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/runtime/pytest_plugin.py tests/unit/test_pytest_plugin.py
git commit -m "feat: pytest plugin for runtime tracing and coverage"
```

---

## Task 18: pytest runner (`runtime/runner.py`)

**Files:**
- Create: `livegraph/runtime/runner.py`
- Test: `tests/unit/test_runner.py`

`run_pytest` spawns `<python> -m pytest` in the target project with the plugin injected and the two env vars set, then loads and returns the observations JSON. It raises a clear error if `coverage` is not importable in the target environment.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_runner.py
import json

import pytest

from livegraph.runtime.runner import run_pytest, RuntimeUnavailable


def test_run_pytest_returns_parsed_observations(tmp_path, monkeypatch):
    # Fake a subprocess run that writes the observations file.
    obs = {"root": str(tmp_path), "runtime_calls": [], "tests": [],
           "coverage": []}

    def fake_run(cmd, env, cwd, check, capture_output, text):
        output = env["LIVEGRAPH_OUTPUT"]
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(obs, handle)
        class _R:
            returncode = 0
            stdout = "1 passed"
            stderr = ""
        return _R()

    monkeypatch.setattr("livegraph.runtime.runner.subprocess.run", fake_run)
    monkeypatch.setattr("livegraph.runtime.runner._coverage_importable",
                        lambda python: True)
    result = run_pytest(str(tmp_path), python="python")
    assert result["root"] == str(tmp_path)
    assert result["tests"] == []


def test_run_pytest_raises_when_coverage_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("livegraph.runtime.runner._coverage_importable",
                        lambda python: False)
    with pytest.raises(RuntimeUnavailable, match="coverage"):
        run_pytest(str(tmp_path), python="python")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.runtime.runner'`.

- [ ] **Step 3: Write `livegraph/runtime/runner.py`**

```python
"""Invoke the target project's pytest suite under the livegraph plugin."""
from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 - required to launch the target's pytest
import sys
import tempfile
from typing import Any


class RuntimeUnavailable(RuntimeError):
    """Phase 2 cannot run (e.g. ``coverage`` missing in the target env)."""


def _coverage_importable(python: str) -> bool:
    """Return True if ``coverage`` can be imported by ``python``."""
    result = subprocess.run(  # noqa: S603
        [python, "-c", "import coverage"],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def run_pytest(root: str, python: str | None = None) -> dict[str, Any]:
    """Run the target's pytest suite traced, return parsed observations.

    ``root`` is the target project directory. ``python`` is the
    interpreter to use (defaults to the current one). The livegraph
    package must be importable by that interpreter — its directory is
    placed on PYTHONPATH for the subprocess.
    """
    python = python or sys.executable
    root = os.path.abspath(root)

    if not _coverage_importable(python):
        raise RuntimeUnavailable(
            f"`coverage` is not importable by {python}. Install it in the "
            f"target environment to enable Phase 2 (pip install coverage)."
        )

    livegraph_pkg_parent = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    with tempfile.TemporaryDirectory() as workdir:
        output_path = os.path.join(workdir, "observations.json")
        env = dict(os.environ)
        env["LIVEGRAPH_ROOT"] = root
        env["LIVEGRAPH_OUTPUT"] = output_path
        # Force coverage.py's C trace core: it supports measurement
        # contexts and leaves sys.monitoring free for the call tracer.
        env["COVERAGE_CORE"] = "ctrace"
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            livegraph_pkg_parent + os.pathsep + existing
            if existing else livegraph_pkg_parent
        )
        cmd = [python, "-m", "pytest", "-p", "livegraph.runtime.pytest_plugin",
               root]
        subprocess.run(  # noqa: S603
            cmd, env=env, cwd=root, check=False,
            capture_output=True, text=True,
        )
        if not os.path.exists(output_path):
            raise RuntimeUnavailable(
                "pytest produced no observations file — the target may have "
                "no tests, or the suite failed to start."
            )
        with open(output_path, encoding="utf-8") as handle:
            return json.load(handle)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_runner.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/livegraph
git add livegraph/runtime/runner.py tests/unit/test_runner.py
git commit -m "feat: subprocess runner for traced pytest execution"
```

---

## Task 19: Phase 2 orchestrator (`augment.py`) + writer extensions

**Files:**
- Modify: `livegraph/graph/writer.py`
- Create: `livegraph/augment.py`
- Test: `tests/unit/test_writer_runtime.py`
- Test: `tests/unit/test_augment.py`

`augment.py` consumes a runtime observations dict, loads the project's definitions back from the graph (to attribute coverage), and writes runtime data: `:Test` labels + outcomes, `CALLS` provenance updates, and `COVERS` edges.

- [ ] **Step 1: Write the failing writer test**

```python
# tests/unit/test_writer_runtime.py
from livegraph.graph.backend import FakeBackend
from livegraph.graph.writer import GraphWriter
from livegraph.models import CoverageRecord, RuntimeCall, TestResult


def test_write_runtime_calls_sets_runtime_provenance():
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).write_runtime_calls(
        [RuntimeCall("a.py::f", "a.py::g", "t.py::test", 0)],
        counts={("a.py::f", "a.py::g"): 4},
    )
    query, params = backend.calls[0]
    assert "MERGE" in query and ":CALLS" in query
    assert params["rows"][0]["runtime"] is True
    assert params["rows"][0]["observed_count"] == 4


def test_write_test_results_adds_test_label():
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).write_test_results(
        [TestResult("t.py::test_x", "passed", 0.02)])
    query, _params = backend.calls[0]
    assert ":Test" in query and "test_outcome" in query


def test_write_coverage_emits_covers_edges():
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).write_coverage(
        [CoverageRecord("t.py::test", "a.py::f", 3, 4)])
    query, params = backend.calls[0]
    assert ":COVERS" in query
    assert params["rows"][0]["coverage_pct"] == 75.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_writer_runtime.py -v`
Expected: FAIL — `AttributeError: 'GraphWriter' object has no attribute 'write_runtime_calls'`.

- [ ] **Step 3: Append runtime write methods to `livegraph/graph/writer.py`**

Add these imports to the existing import block in `writer.py`:

```python
from livegraph.models import CoverageRecord, RuntimeCall, TestResult
```

Add these three methods inside the `GraphWriter` class, after `write_calls`:

```python
    def write_runtime_calls(
        self, calls: Iterable[RuntimeCall],
        counts: dict[tuple[str, str], int],
    ) -> None:
        """MERGE CALLS edges observed at runtime, setting provenance.

        ``counts`` maps a (caller_qn, callee_qn) pair to its observed
        count aggregated across all tests.
        """
        distinct = {(c.caller_qn, c.callee_qn) for c in calls}
        rows_all = [
            {"caller": caller, "callee": callee, "runtime": True,
             "observed_count": counts.get((caller, callee), 0)}
            for caller, callee in distinct
        ]
        for batch in _batched(rows_all, self._batch_size):
            self._backend.execute(
                "UNWIND $rows AS row "
                "MATCH (caller {qualified_name: row.caller}) "
                "MATCH (callee {qualified_name: row.callee}) "
                "MERGE (caller)-[c:CALLS]->(callee) "
                "SET c.runtime = row.runtime, "
                "    c.observed_count = row.observed_count, "
                "    c.static = coalesce(c.static, false)",
                rows=list(batch),
            )

    def write_test_results(self, results: Iterable[TestResult]) -> None:
        """Add the :Test label and outcome to each test's Function node."""
        for batch in _batched(results, self._batch_size):
            rows = [
                {"qualified_name": r.qualified_name, "outcome": r.outcome,
                 "duration": r.duration}
                for r in batch
            ]
            self._backend.execute(
                "UNWIND $rows AS row "
                "MERGE (t:Function {qualified_name: row.qualified_name}) "
                "ON CREATE SET t.runtime_only = true "
                "SET t:Test, t.test_outcome = row.outcome, "
                "    t.test_duration = row.duration",
                rows=rows,
            )

    def write_coverage(self, records: Iterable[CoverageRecord]) -> None:
        """MERGE COVERS edges and aggregate coverage onto symbol nodes."""
        for batch in _batched(records, self._batch_size):
            rows = [
                {"test": r.test_qn, "symbol": r.symbol_qn,
                 "lines_covered": r.lines_covered,
                 "lines_total": r.lines_total,
                 "coverage_pct": r.coverage_pct}
                for r in batch
            ]
            self._backend.execute(
                "UNWIND $rows AS row "
                "MATCH (test {qualified_name: row.test}) "
                "MATCH (symbol {qualified_name: row.symbol}) "
                "MERGE (test)-[c:COVERS]->(symbol) "
                "SET c.lines_covered = row.lines_covered, "
                "    c.lines_total = row.lines_total, "
                "    c.coverage_pct = row.coverage_pct "
                "SET symbol.runtime_observed = true, "
                "    symbol.coverage_pct = row.coverage_pct, "
                "    symbol.lines_covered = row.lines_covered, "
                "    symbol.lines_total = row.lines_total",
                rows=rows,
            )
```

- [ ] **Step 4: Run writer test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_writer_runtime.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Write the failing augment test**

```python
# tests/unit/test_augment.py
from livegraph.augment import augment_from_observations
from livegraph.graph.backend import FakeBackend


def test_augment_writes_calls_tests_and_coverage():
    # The backend returns the project's definitions when queried.
    definitions_rows = [
        {"qualified_name": "calc.py::add", "file": "calc.py",
         "start_line": 1, "end_line": 2, "kind": "function"},
    ]
    backend = FakeBackend(rows=definitions_rows)
    observations = {
        "root": "/tmp/proj",
        "runtime_calls": [
            {"caller_qn": "calc.py::total", "callee_qn": "calc.py::add",
             "test_qn": "calc.py::test_total", "observed_count": 2},
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
    summary = augment_from_observations(observations, backend, batch_size=100)
    assert summary.runtime_call_edges == 1
    assert summary.tests == 1
    assert summary.coverage_edges == 1
    issued = " ".join(q for q, _ in backend.calls)
    assert ":CALLS" in issued and ":Test" in issued and ":COVERS" in issued
```

- [ ] **Step 6: Run augment test to verify it fails**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_augment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.augment'`.

- [ ] **Step 7: Write `livegraph/augment.py`**

```python
"""Phase 2: merge runtime observations into the existing graph."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from livegraph.graph.backend import GraphBackend
from livegraph.graph.writer import GraphWriter
from livegraph.models import CoverageRecord, Definition, RuntimeCall, TestResult
from livegraph.runtime.coverage_adapter import map_coverage_to_symbols

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AugmentSummary:
    """Counts produced by a Phase 2 run."""

    runtime_call_edges: int
    tests: int
    coverage_edges: int


def _load_definitions(backend: GraphBackend) -> list[Definition]:
    """Read function/method definitions back from the graph.

    Phase 2 needs line spans to attribute coverage; they were written
    by Phase 1, so they are read rather than re-parsed.
    """
    rows = backend.execute(
        "MATCH (d) WHERE d:Function OR d:Method "
        "RETURN d.qualified_name AS qualified_name, d.file AS file, "
        "       d.start_line AS start_line, d.end_line AS end_line, "
        "       labels(d) AS labels"
    )
    definitions: list[Definition] = []
    for row in rows:
        kind = "method" if "Method" in (row.get("labels") or []) else "function"
        definitions.append(Definition(
            qualified_name=row["qualified_name"], name="", kind=kind,
            file=row["file"], start_line=row["start_line"] or 0,
            end_line=row["end_line"] or 0, decorators=(), source="",
        ))
    return definitions


def augment_from_observations(
    observations: dict[str, Any], backend: GraphBackend,
    batch_size: int = 1000,
) -> AugmentSummary:
    """Merge a runtime observations dict into the graph."""
    backend.verify()
    writer = GraphWriter(backend, batch_size=batch_size)

    runtime_calls = [
        RuntimeCall(caller_qn=rc["caller_qn"], callee_qn=rc["callee_qn"],
                    test_qn=rc["test_qn"], call_site_line=0)
        for rc in observations.get("runtime_calls", [])
    ]
    counts: dict[tuple[str, str], int] = {}
    for rc in observations.get("runtime_calls", []):
        key = (rc["caller_qn"], rc["callee_qn"])
        counts[key] = counts.get(key, 0) + int(rc["observed_count"])

    tests = [
        TestResult(qualified_name=t["qualified_name"], outcome=t["outcome"],
                   duration=float(t["duration"]))
        for t in observations.get("tests", [])
    ]

    per_test_lines: dict[str, set[tuple[str, int]]] = {}
    for entry in observations.get("coverage", []):
        bucket = per_test_lines.setdefault(entry["test_context"], set())
        for line in entry["lines"]:
            bucket.add((entry["file"], int(line)))
    coverage_records: list[CoverageRecord] = map_coverage_to_symbols(
        per_test_lines, _load_definitions(backend))

    writer.write_test_results(tests)
    writer.write_runtime_calls(runtime_calls, counts)
    writer.write_coverage(coverage_records)

    logger.info("Phase 2: %d runtime calls, %d tests, %d coverage edges",
                len(runtime_calls), len(tests), len(coverage_records))
    return AugmentSummary(
        runtime_call_edges=len(runtime_calls), tests=len(tests),
        coverage_edges=len(coverage_records),
    )
```

- [ ] **Step 8: Run augment test to verify it passes**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_augment.py tests/unit/test_writer.py -v`
Expected: PASS (test_augment 1 passed; test_writer still 4 passed).

- [ ] **Step 9: Commit**

```bash
cd ~/livegraph
git add livegraph/graph/writer.py livegraph/augment.py tests/unit/test_writer_runtime.py tests/unit/test_augment.py
git commit -m "feat: Phase 2 orchestrator and runtime graph writes"
```

---

## Task 20: CLI `trace` + `build`, and the differentiator integration test

**Files:**
- Modify: `livegraph/cli.py`
- Create: `tests/integration/test_phase2_integration.py`

This wires Phase 2 into the CLI and proves the project's core premise: after `build`, the dynamic-dispatch call in the fixture project must appear as a `CALLS` edge with `static=false, runtime=true`.

- [ ] **Step 1: Add `trace` and `build` commands to `livegraph/cli.py`**

Add this import to the top of `cli.py`:

```python
from livegraph.augment import augment_from_observations
from livegraph.runtime.runner import RuntimeUnavailable, run_pytest
```

Add these two commands before the `if __name__` block:

```python
@app.command()
def trace(
    path: str = typer.Argument(..., help="Project root to trace"),
    python: str = typer.Option(None, "--python", help="Target interpreter"),
) -> None:
    """Phase 2: trace the project's pytest suite and augment the graph."""
    settings = load_settings()
    backend = _make_backend()
    try:
        observations = run_pytest(path, python=python)
        summary = augment_from_observations(
            observations, backend, batch_size=settings.livegraph_batch_size)
    except RuntimeUnavailable as exc:
        typer.echo(f"Phase 2 skipped: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        backend.close()
    typer.echo(
        f"Phase 2 complete: {summary.runtime_call_edges} runtime call "
        f"edges, {summary.tests} tests, {summary.coverage_edges} coverage "
        f"edges."
    )


@app.command()
def build(
    path: str = typer.Argument(..., help="Project root to build"),
    python: str = typer.Option(None, "--python", help="Target interpreter"),
) -> None:
    """Run Phase 1 then Phase 2 for the project at PATH."""
    settings = load_settings()
    backend = _make_backend()
    try:
        ingest_summary = ingest_project(
            path, backend, batch_size=settings.livegraph_batch_size)
        typer.echo(
            f"Phase 1 complete: {ingest_summary.files} files, "
            f"{ingest_summary.definitions} definitions."
        )
        try:
            observations = run_pytest(path, python=python)
            augment_summary = augment_from_observations(
                observations, backend,
                batch_size=settings.livegraph_batch_size)
            typer.echo(
                f"Phase 2 complete: "
                f"{augment_summary.runtime_call_edges} runtime call edges, "
                f"{augment_summary.tests} tests."
            )
        except RuntimeUnavailable as exc:
            typer.echo(f"Phase 2 skipped: {exc}", err=True)
    finally:
        backend.close()
```

- [ ] **Step 2: Add a CLI test for `build` to `tests/unit/test_cli.py`**

Append to `tests/unit/test_cli.py`:

```python
def test_build_runs_both_phases(tmp_path, monkeypatch):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setattr(
        cli, "run_pytest",
        lambda path, python=None: {
            "root": str(tmp_path), "runtime_calls": [], "tests": [],
            "coverage": [],
        },
    )
    result = runner.invoke(cli.app, ["build", str(tmp_path)])
    assert result.exit_code == 0
    assert "Phase 1 complete" in result.stdout
    assert "Phase 2 complete" in result.stdout
```

- [ ] **Step 3: Run the CLI unit tests**

Run: `cd ~/livegraph && .venv/bin/pytest tests/unit/test_cli.py -v`
Expected: PASS (4 passed).

- [ ] **Step 4: Write `tests/integration/test_phase2_integration.py`**

```python
"""Phase 2 end-to-end: the differentiator test.

Proves that runtime tracing catches a dynamic-dispatch call that static
analysis cannot resolve.
"""
import sys

import pytest

from livegraph.augment import augment_from_observations
from livegraph.ingest import ingest_project
from livegraph.runtime.runner import run_pytest

pytestmark = pytest.mark.integration


def test_runtime_catches_dynamic_dispatch(neo4j_backend, sample_project_path):
    # Phase 1: static graph.
    ingest_project(sample_project_path, neo4j_backend,
                   project_name="sample", batch_size=100)

    # The static call resolver must NOT have linked run_operation -> add,
    # because `op(a, b)` is dynamic dispatch.
    static_edge = neo4j_backend.execute(
        "MATCH (:Function {qualified_name: 'runner.py::run_operation'})"
        "-[c:CALLS]->(:Method {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN c.static AS static"
    )
    assert static_edge == []   # nothing yet

    # Phase 2: trace the fixture's pytest suite.
    observations = run_pytest(sample_project_path, python=sys.executable)
    augment_from_observations(observations, neo4j_backend, batch_size=100)

    # Now the edge must exist with static=false, runtime=true.
    rows = neo4j_backend.execute(
        "MATCH (:Function {qualified_name: 'runner.py::run_operation'})"
        "-[c:CALLS]->(:Method {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN c.static AS static, c.runtime AS runtime"
    )
    assert len(rows) == 1, "runtime tracing should have found the call"
    assert rows[0]["runtime"] is True
    assert rows[0]["static"] in (False, None)


def test_phase2_writes_test_nodes_and_coverage(
    neo4j_backend, sample_project_path,
):
    ingest_project(sample_project_path, neo4j_backend,
                   project_name="sample", batch_size=100)
    observations = run_pytest(sample_project_path, python=sys.executable)
    augment_from_observations(observations, neo4j_backend, batch_size=100)

    tests = neo4j_backend.execute(
        "MATCH (t:Test) RETURN count(t) AS n")
    assert tests[0]["n"] >= 3   # three tests in the fixture suite

    covers = neo4j_backend.execute(
        "MATCH (:Test)-[c:COVERS]->() RETURN count(c) AS n")
    assert covers[0]["n"] >= 1
```

- [ ] **Step 5: Run the Phase 2 integration test**

Run:
```bash
cd ~/livegraph && docker compose up -d && sleep 5
.venv/bin/pytest tests/integration/test_phase2_integration.py -v -m integration
```
Expected: PASS (2 passed). The first test passing **is the proof the project works**: runtime tracing resolved a call static analysis could not.

- [ ] **Step 6: Run the entire suite**

Run:
```bash
cd ~/livegraph && .venv/bin/pytest -m "not integration" -q
.venv/bin/pytest -m integration -q
```
Expected: all unit tests PASS; all integration tests PASS.

- [ ] **Step 7: Type-check and lint**

Run:
```bash
cd ~/livegraph
.venv/bin/mypy livegraph
.venv/bin/ruff check livegraph
```
Expected: `mypy` reports no errors; `ruff` reports no errors. Fix any reported issues before committing.

- [ ] **Step 8: Commit**

```bash
cd ~/livegraph
git add livegraph/cli.py tests/unit/test_cli.py tests/integration/test_phase2_integration.py
git commit -m "feat: trace and build CLI commands; Phase 2 differentiator test"
```

---

## Done

At this point `livegraph` implements the full design spec scope:

- **Phase 1** — `livegraph ingest <path>` builds the static graph (File/Class/Function/Method/Module nodes; CONTAINS/DEFINES/HAS_METHOD/IMPORTS/CALLS edges).
- **Phase 2** — `livegraph trace <path>` runs the target's pytest suite under tracing and merges runtime call edges, `:Test` labels, and `COVERS` coverage edges.
- **`livegraph build <path>`** runs both; `status` and `clean` manage the graph.
- The differentiator is proven by `test_runtime_catches_dynamic_dispatch`.

Try it: `docker compose up -d && livegraph build .` then open Neo4j Browser at `http://localhost:7474` and run `MATCH (a)-[c:CALLS]->(b) WHERE c.runtime AND NOT c.static RETURN a, c, b` to see the calls static analysis missed.

Out of scope, as designed: the MCP server, NL→Cypher querying, embeddings, non-Python languages, incremental updates (future Phase 3 spec).
