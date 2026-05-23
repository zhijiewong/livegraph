# livegraph Phase 7 — Embeddings + `semantic_search` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 7 — a `livegraph embed` CLI command that computes local sentence-transformer embeddings for every `Function`/`Method` node, plus a `semantic_search` MCP tool (the 14th) that returns code symbols ranked by cosine similarity to a natural-language query. The ML dependency stack is opt-in via a `pip install livegraph[semantic]` extra.

**Architecture:** A new `livegraph/semantic/` package contains `provider.py` (the `EmbeddingProvider` protocol + a `LocalSTProvider` implementation with lazy `sentence_transformers` import) and `embed.py` (the orchestrator: candidate detection, source-hash staleness, batching, write-back, vector-index management). A new `:Symbol` secondary label on every embedded Function/Method anchors a single Neo4j vector index. The `semantic_search` MCP tool composes the provider's `encode()` with `CALL db.index.vector.queryNodes` and post-filters by project scope and kind.

**Tech Stack:** Python 3.12+. New OPTIONAL runtime dep `sentence-transformers>=3.0` (pulls torch transitively, ~2 GB) behind the `[semantic]` pip extra. Lean install unaffected.

**Reference:** Design spec at `docs/superpowers/specs/2026-05-24-livegraph-phase7-embeddings-design.md`.

**Conventions for every task:**
- Run from the repo root: `cd /Users/yvon.zhu/Documents/GitHub/livegraph`.
- Unit tests need no Neo4j and no `[semantic]` extra (mocks substitute). Integration tests are `@pytest.mark.integration` AND `@pytest.mark.semantic`; they need Neo4j up *and* the extra installed.
- If git complains about identity, use `git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit ...`.
- All work happens on a feature branch (`implement-phase-7-embeddings`) created in Task 1.

---

## Task 1: Branch + sanity check

**Files:** None (branch only).

- [ ] **Step 1: Create the feature branch**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
git checkout main
git pull --ff-only
git checkout -b implement-phase-7-embeddings
```

- [ ] **Step 2: Sanity-check the existing suite**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 188 passed (Phase 6 baseline), no errors.

## Report
Status (DONE / BLOCKED), exact pytest output, current branch.

---

## Task 2: Add `livegraph_embed_model` and `livegraph_embed_batch_size` to Settings

**Files:**
- Modify: `livegraph/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Append failing tests to `tests/unit/test_config.py`**

```python
def test_embed_model_default(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_EMBED_MODEL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.livegraph_embed_model == "all-MiniLM-L6-v2"


def test_embed_model_from_env(monkeypatch):
    monkeypatch.setenv("LIVEGRAPH_EMBED_MODEL", "microsoft/unixcoder-base")
    settings = Settings(_env_file=None)
    assert settings.livegraph_embed_model == "microsoft/unixcoder-base"


def test_embed_batch_size_default(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_EMBED_BATCH_SIZE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.livegraph_embed_batch_size == 32


def test_embed_batch_size_from_env(monkeypatch):
    monkeypatch.setenv("LIVEGRAPH_EMBED_BATCH_SIZE", "64")
    settings = Settings(_env_file=None)
    assert settings.livegraph_embed_batch_size == 64
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_config.py -v 2>&1 | tail -10
```
Expected: 4 new failures (`AttributeError`).

- [ ] **Step 3: Add the fields to `livegraph/config.py`**

In `livegraph/config.py`, inside the `Settings` class, after the `livegraph_query_timeout_seconds` line (added in Phase 6), append:

```python
    livegraph_embed_model: str = "all-MiniLM-L6-v2"
    livegraph_embed_batch_size: int = 32
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_config.py -v 2>&1 | tail -10
```
Expected: 12 passed (8 existing + 4 new).

- [ ] **Step 5: Full suite verify**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 192 passed.

- [ ] **Step 6: Commit**

```bash
git add livegraph/config.py tests/unit/test_config.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: add livegraph_embed_model and livegraph_embed_batch_size settings"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 3: Add `[semantic]` optional extra to `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

This task does NOT install the extra — it just declares it. Installation is the user's choice and the integration tests (Task 9) instruct on how to install it.

- [ ] **Step 1: Edit `pyproject.toml`**

Find the existing `[project.optional-dependencies]` section. It currently has just `dev = [...]`. Add the `semantic` extra alongside it:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.3", "mypy>=1.13", "ruff>=0.8"]
semantic = ["sentence-transformers>=3.0"]
```

(Preserve the existing `dev` line exactly as it is.)

- [ ] **Step 2: Verify the project still installs as-is (lean install unaffected)**

```bash
.venv/bin/pip install -e . 2>&1 | tail -3
```
Expected: `Successfully installed livegraph-0.1.0` (or "Requirement already satisfied"). No torch installation. No sentence-transformers installation. The `[semantic]` extra is declared but not pulled by `-e .` alone.

- [ ] **Step 3: Confirm livegraph imports cleanly without sentence-transformers**

```bash
.venv/bin/python -c "import livegraph; import livegraph.mcp.server; import livegraph.ingest; print('OK')"
```
Expected: `OK`. Any ImportError indicates we accidentally added a top-level dependency on sentence-transformers (which would defeat the opt-in design).

- [ ] **Step 4: Confirm sentence-transformers is NOT importable** (the opt-in promise)

```bash
.venv/bin/python -c "import sentence_transformers" 2>&1 | tail -1
```
Expected: `ModuleNotFoundError: No module named 'sentence_transformers'`. If sentence-transformers turns out to already be installed (e.g., transitively from another dep), document that and proceed.

- [ ] **Step 5: Run the unit suite to confirm no regressions**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 192 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: add [semantic] optional pip extra for sentence-transformers"
```

## Report
Status, output of each verification step, commit SHA.

---

## Task 4: `EmbeddingProvider` protocol + `LocalSTProvider` + exceptions

**Files:**
- Create: `livegraph/semantic/__init__.py` (empty)
- Create: `livegraph/semantic/provider.py`
- Test: `tests/unit/test_embedding_provider.py`

The provider's `sentence_transformers` import lives inside a helper function so tests can monkeypatch it without installing the extra.

- [ ] **Step 1: Create the empty package init**

```bash
mkdir -p livegraph/semantic
touch livegraph/semantic/__init__.py
```

- [ ] **Step 2: Write the failing tests** at `tests/unit/test_embedding_provider.py`:

```python
import pytest

from livegraph.semantic.provider import (
    EmbeddingExtraMissing, EmbeddingDimensionMismatch,
    LocalSTProvider,
)


def test_extra_missing_exception_carries_install_hint():
    err = EmbeddingExtraMissing("install hint")
    assert "install" in str(err).lower()


def test_dimension_mismatch_exception_carries_dimensions():
    err = EmbeddingDimensionMismatch(384, 768)
    assert err.existing == 384
    assert err.new == 768
    assert "384" in str(err) and "768" in str(err)
    assert "--rebuild" in str(err)


def test_local_st_provider_raises_when_extra_missing(monkeypatch):
    """When _import_sentence_transformers fails, the provider must raise
    EmbeddingExtraMissing with a clear install hint."""
    def _raise_import_error():
        raise ImportError("No module named 'sentence_transformers'")

    monkeypatch.setattr(
        "livegraph.semantic.provider._import_sentence_transformers",
        _raise_import_error,
    )
    with pytest.raises(EmbeddingExtraMissing) as exc:
        LocalSTProvider(model_name="all-MiniLM-L6-v2")
    assert "semantic" in str(exc.value).lower()


def test_local_st_provider_constructs_with_mocked_st(monkeypatch):
    """With sentence-transformers mocked, the provider exposes name and
    dimensions and forwards encode() through the model."""
    encoded_calls: list[list[str]] = []

    class _FakeModel:
        def get_sentence_embedding_dimension(self) -> int:
            return 384

        def encode(self, texts, batch_size=32):
            encoded_calls.append(list(texts))
            # sentence-transformers returns an np.ndarray; mimic the
            # `.tolist()` API the provider calls on it.
            class _FakeArr:
                def __init__(self, data):
                    self._data = data
                def tolist(self):
                    return self._data
            return _FakeArr([[0.1] * 384 for _ in texts])

    def _fake_import():
        def _fake_factory(model_name):
            assert model_name == "all-MiniLM-L6-v2"
            return _FakeModel()
        return _fake_factory

    monkeypatch.setattr(
        "livegraph.semantic.provider._import_sentence_transformers",
        _fake_import,
    )

    provider = LocalSTProvider(model_name="all-MiniLM-L6-v2",
                               batch_size=16)
    assert provider.name == "all-MiniLM-L6-v2"
    assert provider.dimensions == 384
    assert provider.batch_size == 16

    vectors = provider.encode(["hello world", "foo bar"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 384
    assert encoded_calls == [["hello world", "foo bar"]]


def test_local_st_provider_encode_empty_returns_empty(monkeypatch):
    """encode([]) must short-circuit and never call the model."""
    encoded_calls: list[list[str]] = []

    class _FakeModel:
        def get_sentence_embedding_dimension(self) -> int:
            return 384
        def encode(self, texts, batch_size=32):
            encoded_calls.append(list(texts))
            return []

    monkeypatch.setattr(
        "livegraph.semantic.provider._import_sentence_transformers",
        lambda: (lambda model_name: _FakeModel()),
    )
    provider = LocalSTProvider(model_name="all-MiniLM-L6-v2")
    assert provider.encode([]) == []
    assert encoded_calls == []
```

- [ ] **Step 3: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_embedding_provider.py -v 2>&1 | tail -10
```
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.semantic.provider'`.

- [ ] **Step 4: Write `livegraph/semantic/provider.py`**

```python
"""Embedding-provider protocol and the sentence-transformers implementation.

The sentence_transformers import is performed inside
``_import_sentence_transformers`` so the rest of livegraph can be imported
without the [semantic] extra installed. Tests monkeypatch that helper to
simulate both 'extra installed' and 'extra missing' states.
"""
from __future__ import annotations

from typing import Any, Protocol


class EmbeddingExtraMissing(Exception):
    """The [semantic] pip extra is not installed in this Python env."""


class EmbeddingDimensionMismatch(Exception):
    """Existing vector index has different dimensions than the new model."""

    def __init__(self, existing: int, new: int) -> None:
        super().__init__(
            f"Existing vector index uses {existing} dimensions; the "
            f"configured model produces {new}. Pass --rebuild to drop "
            f"the existing index and start over."
        )
        self.existing = existing
        self.new = new


class EmbeddingProvider(Protocol):
    """Minimal interface every embedding provider must satisfy."""

    name: str
    dimensions: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...


def _import_sentence_transformers() -> Any:
    """Return the ``SentenceTransformer`` class, or raise on missing extra.

    Isolated so unit tests can monkeypatch it without installing torch.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingExtraMissing(
            "sentence-transformers is not installed. Install the optional "
            "extra: pip install 'livegraph[semantic]'"
        ) from exc
    return SentenceTransformer


class LocalSTProvider:
    """``EmbeddingProvider`` backed by a locally-loaded sentence-transformer.

    The model is loaded eagerly on construction; the surrounding code is
    responsible for deferring construction until embeddings are actually
    needed (the [semantic] extra may be absent).
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2",
                 batch_size: int = 32) -> None:
        SentenceTransformerCls = _import_sentence_transformers()
        self._model = SentenceTransformerCls(model_name)
        self.name = model_name
        self.batch_size = batch_size
        self.dimensions = int(
            self._model.get_sentence_embedding_dimension()
        )

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(texts, batch_size=self.batch_size)
        return vectors.tolist()
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/unit/test_embedding_provider.py -v 2>&1 | tail -10
```
Expected: 5 passed.

- [ ] **Step 6: Full suite verify**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 197 passed.

- [ ] **Step 7: Commit**

```bash
git add livegraph/semantic/__init__.py livegraph/semantic/provider.py tests/unit/test_embedding_provider.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: EmbeddingProvider protocol + LocalSTProvider with lazy import"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 5: `embed.py` orchestrator (the meaty Phase 7 task)

**Files:**
- Create: `livegraph/semantic/embed.py`
- Test: `tests/unit/test_embed_orchestrator.py`

Uses a queued-backend pattern (same as Phase 4's `change_impact`) so the multi-query flow is testable end-to-end without Neo4j.

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_embed_orchestrator.py`:

```python
import hashlib
from typing import Any

import pytest

from livegraph.semantic.embed import (
    EmbedSummary, embed_project, INDEX_NAME,
)
from livegraph.semantic.provider import (
    EmbeddingDimensionMismatch,
)


class _MockProvider:
    """Deterministic stand-in for LocalSTProvider."""

    def __init__(self, name="mock-model", dimensions=384, batch_size=32):
        self.name = name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.encode_calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.encode_calls.append(list(texts))
        return [[float(i)] * self.dimensions for i, _ in enumerate(texts)]


class _QueuedBackend:
    """Per-call canned responses (mirrors Phase 4's pattern)."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def verify(self): return None
    def close(self): return None

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        if not self._responses:
            return []
        return self._responses.pop(0)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_embed_all_new_writes_every_candidate():
    """First-time embed: every candidate's source gets embedded + written."""
    provider = _MockProvider()
    src_a = "def f():\n    return 1\n"
    src_b = "def g():\n    return 2\n"
    backend = _QueuedBackend([
        [],   # _read_existing_index_dimensions: no index yet
        [     # candidates query
            {"qn": "a.py::f", "source": src_a,
             "prior_hash": None, "prior_model": None},
            {"qn": "a.py::g", "source": src_b,
             "prior_hash": None, "prior_model": None},
        ],
        [],   # write batch
        [],   # CREATE INDEX
        [],   # CALL db.awaitIndexes()
    ])

    summary = embed_project(backend, project="sample", provider=provider)

    assert summary.embedded == 2
    assert summary.unchanged == 0
    assert summary.skipped == 0
    # The model was called exactly once with both sources.
    assert provider.encode_calls == [[src_a, src_b]]
    # The write batch sent the correct rows.
    write_calls = [c for c in backend.calls if "SET s:Symbol" in c[0]]
    assert len(write_calls) == 1
    rows = write_calls[0][1]["rows"]
    assert {r["qn"] for r in rows} == {"a.py::f", "a.py::g"}
    assert rows[0]["model"] == "mock-model"
    assert rows[0]["hash"] == _hash(src_a)


def test_embed_no_op_when_hashes_unchanged():
    """Re-running embed with unchanged sources writes nothing."""
    provider = _MockProvider()
    src = "def f():\n    return 1\n"
    h = _hash(src)
    backend = _QueuedBackend([
        [{"dimensions": 384}],   # existing index, matching dimensions
        [
            {"qn": "a.py::f", "source": src,
             "prior_hash": h, "prior_model": "mock-model"},
        ],
    ])

    summary = embed_project(backend, project="sample", provider=provider)

    assert summary.embedded == 0
    assert summary.unchanged == 1
    # Provider must not have been called at all.
    assert provider.encode_calls == []
    # No write call should have been issued.
    assert not any("SET s:Symbol" in c[0] for c in backend.calls)


def test_embed_only_changed_source():
    """When one symbol's source hash differs, only that one is re-embedded."""
    provider = _MockProvider()
    src_old = "def f():\n    return 1\n"
    src_new = "def f():\n    return 2\n"
    src_b = "def g():\n    return 3\n"
    h_b = _hash(src_b)
    backend = _QueuedBackend([
        [{"dimensions": 384}],
        [
            {"qn": "a.py::f", "source": src_new,
             "prior_hash": _hash(src_old), "prior_model": "mock-model"},
            {"qn": "a.py::g", "source": src_b,
             "prior_hash": h_b, "prior_model": "mock-model"},
        ],
        [],   # write batch
    ])

    summary = embed_project(backend, project="sample", provider=provider)

    assert summary.embedded == 1
    assert summary.unchanged == 1
    assert provider.encode_calls == [[src_new]]


def test_embed_skips_empty_source():
    """Symbols with empty/None source are skipped, not embedded."""
    provider = _MockProvider()
    backend = _QueuedBackend([
        [{"dimensions": 384}],
        [
            {"qn": "a.py::f", "source": "",
             "prior_hash": None, "prior_model": None},
            {"qn": "a.py::g", "source": "def g(): pass\n",
             "prior_hash": None, "prior_model": None},
        ],
        [],
    ])

    summary = embed_project(backend, project="sample", provider=provider)

    assert summary.embedded == 1
    assert summary.skipped == 1
    assert provider.encode_calls == [["def g(): pass\n"]]


def test_embed_refuses_dimension_mismatch():
    """If the existing index has different dims than the model, refuse."""
    provider = _MockProvider(dimensions=768)
    backend = _QueuedBackend([
        [{"dimensions": 384}],   # existing index is 384, model is 768
    ])

    with pytest.raises(EmbeddingDimensionMismatch) as exc:
        embed_project(backend, project="sample", provider=provider)
    assert exc.value.existing == 384
    assert exc.value.new == 768
    # No write or candidate query should have been issued.
    assert not any("MATCH" in c[0] and "qualified_name" in c[0]
                   for c in backend.calls)


def test_embed_rebuild_clears_then_re_embeds():
    """--rebuild drops the index, clears properties + label, then re-embeds."""
    provider = _MockProvider()
    src = "def f():\n    return 1\n"
    backend = _QueuedBackend([
        [],   # DROP INDEX (no return)
        [],   # REMOVE label + properties
        [],   # _read_existing_index_dimensions: index is gone now
        [
            {"qn": "a.py::f", "source": src,
             "prior_hash": _hash(src), "prior_model": "mock-model"},
            # Even though hash + model match, rebuild forces re-embed.
            # The orchestrator achieves this by clearing prior_hash/model
            # in the REMOVE step BEFORE the candidates query — so when the
            # candidates query runs, those rows come back as None/None.
            # Production behavior; in this test we simulate that by
            # adjusting the prior values to None.
        ],
        [],   # write batch
        [],   # CREATE INDEX
        [],   # CALL db.awaitIndexes()
    ])
    # Adjust the test: with --rebuild, the candidate query sees
    # cleared prior values. Tweak the response to reflect that.
    backend._responses[3] = [{
        "qn": "a.py::f", "source": src,
        "prior_hash": None, "prior_model": None,
    }]

    summary = embed_project(backend, project="sample",
                            provider=provider, rebuild=True)

    assert summary.embedded == 1
    # The DROP INDEX call should have been issued first.
    assert "DROP" in backend.calls[0][0]
    # The REMOVE call should be next.
    assert "REMOVE" in backend.calls[1][0]
    # And the model was called.
    assert provider.encode_calls == [[src]]


def test_embed_creates_vector_index_with_model_dimensions():
    """The CREATE VECTOR INDEX statement uses provider.dimensions."""
    provider = _MockProvider(dimensions=768)
    backend = _QueuedBackend([
        [],   # no existing index
        [{"qn": "a.py::f", "source": "x = 1\n",
          "prior_hash": None, "prior_model": None}],
        [],   # write batch
        [],   # CREATE INDEX
        [],   # CALL db.awaitIndexes()
    ])

    embed_project(backend, project="sample", provider=provider)

    create_calls = [c for c in backend.calls if "CREATE VECTOR INDEX" in c[0]]
    assert create_calls, "expected a CREATE VECTOR INDEX call"
    create_cypher, _params = create_calls[0]
    assert "`vector.dimensions`: 768" in create_cypher
    assert INDEX_NAME in create_cypher
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_embed_orchestrator.py -v 2>&1 | tail -10
```
Expected: FAIL — `ModuleNotFoundError: No module named 'livegraph.semantic.embed'`.

- [ ] **Step 3: Write `livegraph/semantic/embed.py`**

```python
"""Embed orchestrator: detect candidates, batch through provider, write back.

Two-phase (mirrors Phase 5's pattern):
  Phase A — query candidates and filter to those needing (re-)embedding
            by comparing the current source hash and the configured model
            against what's stored on each node.
  Phase B — batched UNWIND writes setting :Symbol label, embedding,
            embedding_source_hash, and embedding_model on each row, then
            CREATE VECTOR INDEX IF NOT EXISTS and await it.

``--rebuild`` adds a Phase 0: drop the index, clear :Symbol labels and
embedding* properties for the project.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from livegraph.graph.backend import GraphBackend
from livegraph.semantic.provider import (
    EmbeddingDimensionMismatch, EmbeddingProvider,
)

INDEX_NAME = "livegraph_symbol_embeddings"


@dataclass(frozen=True, slots=True)
class EmbedSummary:
    """Counts produced by an embed_project run."""

    embedded: int
    unchanged: int
    skipped: int


_CANDIDATES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE (s:Function OR s:Method) "
    "RETURN s.qualified_name AS qn, "
    "       coalesce(s.source, '') AS source, "
    "       s.embedding_source_hash AS prior_hash, "
    "       s.embedding_model AS prior_model "
    "ORDER BY s.qualified_name"
)


_WRITE_CYPHER = (
    "UNWIND $rows AS row "
    "MATCH (s {qualified_name: row.qn}) "
    "WHERE s:Function OR s:Method "
    "SET s:Symbol, "
    "    s.embedding = row.embedding, "
    "    s.embedding_source_hash = row.hash, "
    "    s.embedding_model = row.model"
)


_DROP_INDEX = f"DROP INDEX {INDEX_NAME} IF EXISTS"


_REMOVE_PROJECT_EMBEDDINGS = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE s:Symbol "
    "REMOVE s:Symbol, s.embedding, s.embedding_source_hash, "
    "       s.embedding_model"
)


_SHOW_INDEX = (
    "SHOW INDEXES YIELD name, type, options "
    "WHERE name = $name AND type = 'VECTOR' "
    "RETURN options AS options"
)


def _read_existing_index_dimensions(backend: GraphBackend) -> int | None:
    """Return the existing vector index's dimensions, or None if absent."""
    rows = backend.execute(_SHOW_INDEX, name=INDEX_NAME)
    if not rows:
        return None
    options = rows[0].get("options") or {}
    # Driver may return a dict already; if it returns the bare dimensions
    # we accept either shape.
    config = options.get("indexConfig", options) or {}
    dims = config.get("vector.dimensions") or options.get("dimensions")
    return int(dims) if dims is not None else None


def _create_index_cypher(dimensions: int) -> str:
    return (
        f"CREATE VECTOR INDEX {INDEX_NAME} IF NOT EXISTS "
        f"FOR (n:Symbol) ON (n.embedding) "
        f"OPTIONS {{indexConfig: {{"
        f"`vector.dimensions`: {int(dimensions)}, "
        f"`vector.similarity_function`: 'cosine'"
        f"}}}}"
    )


def _hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _batched(items: list, size: int) -> Iterable[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def embed_project(
    backend: GraphBackend, project: str, provider: EmbeddingProvider,
    rebuild: bool = False,
) -> EmbedSummary:
    """Embed every Function/Method in the project that needs (re-)embedding."""
    if rebuild:
        backend.execute(_DROP_INDEX)
        backend.execute(_REMOVE_PROJECT_EMBEDDINGS, project=project)

    existing_dims = _read_existing_index_dimensions(backend)
    if existing_dims is not None and existing_dims != provider.dimensions:
        raise EmbeddingDimensionMismatch(existing_dims, provider.dimensions)

    candidates = backend.execute(_CANDIDATES_CYPHER, project=project)

    to_embed: list[tuple[str, str, str]] = []   # (qn, source, hash)
    unchanged = 0
    skipped = 0
    for row in candidates:
        source = row.get("source") or ""
        if not source:
            skipped += 1
            continue
        current_hash = _hash(source)
        if (row.get("prior_hash") == current_hash
                and row.get("prior_model") == provider.name):
            unchanged += 1
            continue
        to_embed.append((row["qn"], source, current_hash))

    embedded_count = 0
    if to_embed:
        for batch in _batched(to_embed, provider.batch_size):
            texts = [item[1] for item in batch]
            vectors = provider.encode(texts)
            rows = [
                {"qn": qn, "embedding": vec, "hash": h,
                 "model": provider.name}
                for (qn, _src, h), vec in zip(batch, vectors)
            ]
            backend.execute(_WRITE_CYPHER, rows=rows)
            embedded_count += len(rows)

        backend.execute(_create_index_cypher(provider.dimensions))
        backend.execute("CALL db.awaitIndexes()")

    return EmbedSummary(
        embedded=embedded_count, unchanged=unchanged, skipped=skipped,
    )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_embed_orchestrator.py -v 2>&1 | tail -15
```
Expected: 7 passed.

- [ ] **Step 5: Full suite verify**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 204 passed.

- [ ] **Step 6: Commit**

```bash
git add livegraph/semantic/embed.py tests/unit/test_embed_orchestrator.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: embed_project orchestrator with source-hash staleness"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 6: `livegraph embed` CLI subcommand

**Files:**
- Modify: `livegraph/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Append failing tests to `tests/unit/test_cli.py`**

```python
def test_embed_command_errors_when_project_missing(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    result = runner.invoke(cli.app, ["embed"])
    assert result.exit_code != 0
    assert "LIVEGRAPH_PROJECT" in (result.output + (result.stderr or ""))


def test_embed_command_handles_missing_extra(monkeypatch):
    """When LocalSTProvider raises EmbeddingExtraMissing, CLI exits cleanly."""
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "p")

    from livegraph.semantic.provider import EmbeddingExtraMissing

    def fake_make_provider(*args, **kwargs):
        raise EmbeddingExtraMissing(
            "sentence-transformers is not installed. Install the optional "
            "extra: pip install 'livegraph[semantic]'"
        )

    monkeypatch.setattr("livegraph.cli._make_embedding_provider",
                       fake_make_provider)
    result = runner.invoke(cli.app, ["embed"])
    assert result.exit_code == 1
    assert "livegraph[semantic]" in (result.output + (result.stderr or ""))


def test_embed_command_invokes_embed_project(monkeypatch, tmp_path):
    """Happy path: CLI calls embed_project with backend + project + provider."""
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "sample")

    class _FakeProvider:
        name = "mock-model"
        dimensions = 384
        batch_size = 32

    monkeypatch.setattr("livegraph.cli._make_embedding_provider",
                       lambda settings: _FakeProvider())

    captured: dict = {}
    def fake_embed(backend_arg, project, provider, rebuild=False):
        captured["project"] = project
        captured["rebuild"] = rebuild
        captured["provider_name"] = provider.name
        from livegraph.semantic.embed import EmbedSummary
        return EmbedSummary(embedded=3, unchanged=2, skipped=0)

    monkeypatch.setattr("livegraph.cli.embed_project", fake_embed)
    result = runner.invoke(cli.app, ["embed"])
    assert result.exit_code == 0
    assert captured["project"] == "sample"
    assert captured["rebuild"] is False
    assert captured["provider_name"] == "mock-model"
    # Output should mention counts.
    assert "3" in result.output and "2" in result.output


def test_embed_command_rebuild_flag(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "p")

    class _FakeProvider:
        name = "m"; dimensions = 384; batch_size = 32

    monkeypatch.setattr("livegraph.cli._make_embedding_provider",
                       lambda settings: _FakeProvider())

    captured: dict = {}
    def fake_embed(*args, rebuild=False, **kwargs):
        captured["rebuild"] = rebuild
        from livegraph.semantic.embed import EmbedSummary
        return EmbedSummary(0, 0, 0)

    monkeypatch.setattr("livegraph.cli.embed_project", fake_embed)
    result = runner.invoke(cli.app, ["embed", "--rebuild"])
    assert result.exit_code == 0
    assert captured["rebuild"] is True
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_cli.py -v 2>&1 | tail -10
```
Expected: failures (`Error: No such command 'embed'`).

- [ ] **Step 3: Modify `livegraph/cli.py`**

Add this import near the top, after the existing imports:

```python
from livegraph.semantic.embed import embed_project
from livegraph.semantic.provider import (
    EmbeddingExtraMissing, EmbeddingDimensionMismatch, LocalSTProvider,
)
```

Add a helper function (anywhere in the module body, e.g. right after `_resolve_root_path`):

```python
def _make_embedding_provider(settings):
    """Build a LocalSTProvider from configured Settings.

    Isolated in its own function so tests can monkeypatch it without
    installing the [semantic] extra.
    """
    return LocalSTProvider(
        model_name=settings.livegraph_embed_model,
        batch_size=settings.livegraph_embed_batch_size,
    )
```

Add this command before the `if __name__ == "__main__":` block:

```python
@app.command()
def embed(
    project: str = typer.Option(
        None, "--project",
        help="Ingested project to embed (overrides LIVEGRAPH_PROJECT env)",
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild",
        help="Drop the vector index, clear all embeddings, then re-embed",
    ),
) -> None:
    """Compute embeddings for every Function/Method in the project."""
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
        try:
            provider = _make_embedding_provider(settings)
        except EmbeddingExtraMissing as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

        typer.echo(
            f"Project: {resolved_project}\n"
            f"Loading model: {provider.name} "
            f"({provider.dimensions} dims)... done."
        )
        try:
            summary = embed_project(
                backend, resolved_project, provider, rebuild=rebuild,
            )
        except EmbeddingDimensionMismatch as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc

        typer.echo(
            f"Embed complete: {summary.embedded} embedded, "
            f"{summary.unchanged} unchanged, "
            f"{summary.skipped} skipped."
        )
    finally:
        backend.close()
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_cli.py -v 2>&1 | tail -15
```
Expected: all existing CLI tests + 4 new tests pass.

- [ ] **Step 5: Full suite verify**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 208 passed.

- [ ] **Step 6: Commit**

```bash
git add livegraph/cli.py tests/unit/test_cli.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: livegraph embed CLI subcommand"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 7: `semantic_search` MCP tool function

**Files:**
- Modify: `livegraph/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools_semantic_search.py`

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_mcp_tools_semantic_search.py`:

```python
from typing import Any

from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import semantic_search


class _MockProvider:
    name = "mock-model"
    dimensions = 384
    batch_size = 32

    def __init__(self):
        self.encode_calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.encode_calls.append(list(texts))
        return [[0.5] * 384 for _ in texts]


def test_semantic_search_returns_documented_keys():
    backend = FakeBackend(rows=[
        {"qualified_name": "a.py::f", "name": "f", "kind": "function",
         "file": "a.py", "start_line": 1, "end_line": 5,
         "source": "def f():\n    return 1\n", "score": 0.92},
    ])
    # FakeBackend returns the same rows for every execute call. The
    # semantic_search query path is the FIRST execute. The
    # _count_embedded read after is the SECOND. Both get the same rows;
    # _count_embedded will see a row count of 1 (not the {"n": N} shape).
    # For this test we focus on the top-level shape, not embedded_count.
    provider = _MockProvider()
    result = semantic_search(backend, project="sample", provider=provider,
                            query="addition")
    assert "results" in result
    assert "model" in result
    assert "embedded_count" in result
    assert "warning" in result
    assert result["model"] == "mock-model"


def test_semantic_search_returns_empty_when_index_missing():
    """No index → results=[] + warning, no provider.encode() call."""
    # FakeBackend's _index_exists check (we test for an empty result) shows
    # the index isn't there; we expect the function to short-circuit.
    backend = FakeBackend(rows=[])   # SHOW INDEXES returns nothing
    provider = _MockProvider()
    result = semantic_search(backend, project="sample", provider=provider,
                            query="anything")
    assert result["results"] == []
    assert "no embeddings" in (result.get("warning") or "").lower()
    assert provider.encode_calls == []


def test_semantic_search_calls_provider_encode_with_query():
    """When the index exists, the provider is called with the query string."""
    # Test trick: FakeBackend returns the same rows for every execute call.
    # We give it a non-empty SHOW INDEXES response so _index_exists() is True;
    # the subsequent query call also gets the same rows but we don't assert
    # on the embedded_count value here.
    backend = FakeBackend(rows=[
        {"name": "livegraph_symbol_embeddings", "type": "VECTOR"},
    ])
    provider = _MockProvider()
    semantic_search(backend, project="sample", provider=provider,
                    query="how authentication works")
    assert provider.encode_calls == [["how authentication works"]]


def test_semantic_search_passes_kind_filter():
    backend = FakeBackend(rows=[
        {"name": "livegraph_symbol_embeddings", "type": "VECTOR"},
    ])
    provider = _MockProvider()
    semantic_search(backend, project="sample", provider=provider,
                    query="x", kind="function")
    # Find the queryNodes call.
    query_calls = [c for c in backend.calls
                  if "db.index.vector.queryNodes" in c[0]]
    assert query_calls, "expected a vector query call"
    _q, params = query_calls[0]
    assert params["kind"] == "function"


def test_semantic_search_passes_limit():
    backend = FakeBackend(rows=[
        {"name": "livegraph_symbol_embeddings", "type": "VECTOR"},
    ])
    provider = _MockProvider()
    semantic_search(backend, project="sample", provider=provider,
                    query="x", limit=5)
    query_calls = [c for c in backend.calls
                  if "db.index.vector.queryNodes" in c[0]]
    assert query_calls
    _q, params = query_calls[0]
    assert params["limit"] == 5
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_semantic_search.py -v 2>&1 | tail -15
```
Expected: FAIL — `ImportError: cannot import name 'semantic_search'`.

- [ ] **Step 3: Append to `livegraph/mcp/tools.py`**

Add this near the existing imports (after the other Phase 6 imports):

```python
from livegraph.semantic.embed import INDEX_NAME
from livegraph.semantic.provider import (
    EmbeddingExtraMissing, EmbeddingProvider,
)
```

Then append at the end of `livegraph/mcp/tools.py`:

```python
# -- semantic_search --------------------------------------------------

_INDEX_EXISTS_CYPHER = (
    "SHOW INDEXES YIELD name, type "
    "WHERE name = $name AND type = 'VECTOR' "
    "RETURN name"
)


_EMBEDDED_COUNT_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s:Symbol) "
    "RETURN count(DISTINCT s) AS n"
)


_VECTOR_QUERY_CYPHER = (
    "CALL db.index.vector.queryNodes($index_name, $k_padded, $query_vector) "
    "YIELD node, score "
    "WITH node, score "
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(node) "
    "WHERE ($kind = 'any' AND (node:Function OR node:Method)) "
    "   OR ($kind = 'function' AND node:Function AND NOT node:Test) "
    "   OR ($kind = 'method' AND node:Method) "
    "RETURN node.qualified_name AS qualified_name, "
    "       node.name AS name, "
    "       head([l IN labels(node) "
    "             WHERE l IN ['Function','Method'] | toLower(l)]) AS kind, "
    "       node.file AS file, "
    "       node.start_line AS start_line, "
    "       node.end_line AS end_line, "
    "       coalesce(node.source, '') AS source, "
    "       score "
    "ORDER BY score DESC "
    "LIMIT $limit"
)


def _snippet(source: str, lines: int = 3) -> str:
    """First ``lines`` non-blank lines of ``source``, joined with newlines."""
    out: list[str] = []
    for raw_line in source.splitlines():
        if raw_line.strip():
            out.append(raw_line)
            if len(out) >= lines:
                break
    return "\n".join(out)


def _index_exists(backend: GraphBackend) -> bool:
    rows = backend.execute(_INDEX_EXISTS_CYPHER, name=INDEX_NAME)
    return bool(rows)


def _embedded_count(backend: GraphBackend, project: str) -> int:
    rows = backend.execute(_EMBEDDED_COUNT_CYPHER, project=project)
    if not rows:
        return 0
    return int(rows[0].get("n") or 0)


def semantic_search(
    backend: GraphBackend, project: str, provider: EmbeddingProvider,
    query: str, limit: int = 10, kind: str = "any",
) -> dict[str, Any]:
    """Find code symbols by vector similarity to ``query``.

    Returns ``{results, model, embedded_count, warning}``. If the vector
    index does not yet exist (the project has never been embedded), returns
    an empty results list with a warning suggesting ``livegraph embed``.
    """
    if not _index_exists(backend):
        return {
            "results": [],
            "model": provider.name,
            "embedded_count": 0,
            "warning": "no embeddings yet; run `livegraph embed` first",
        }

    query_vector = provider.encode([query])[0]
    k_padded = limit + 50

    rows = backend.execute(
        _VECTOR_QUERY_CYPHER,
        index_name=INDEX_NAME, project=project,
        k_padded=k_padded, query_vector=query_vector,
        kind=kind, limit=limit,
    )

    results = [
        {
            "qualified_name": r["qualified_name"],
            "name": r["name"],
            "kind": r["kind"],
            "file": r["file"],
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "score": float(r.get("score") or 0.0),
            "snippet": _snippet(r.get("source") or ""),
        }
        for r in rows
    ]

    return {
        "results": results,
        "model": provider.name,
        "embedded_count": _embedded_count(backend, project),
        "warning": None,
    }
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_tools_semantic_search.py -v 2>&1 | tail -10
```
Expected: 5 passed.

- [ ] **Step 5: Full suite verify**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 213 passed.

- [ ] **Step 6: Commit**

```bash
git add livegraph/mcp/tools.py tests/unit/test_mcp_tools_semantic_search.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: semantic_search MCP tool function"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 8: Register `semantic_search` as tool 14

**Files:**
- Modify: `livegraph/mcp/server.py`
- Test: `tests/unit/test_mcp_server.py`
- Modify: `tests/integration/test_mcp_server_smoke.py` (expected tool list)

The server now needs to lazy-load the embedding provider on first `semantic_search` call. The `[semantic]` extra absence is handled by returning a structured response with the install hint in the `warning` field.

- [ ] **Step 1: Update the tool-count test** in `tests/unit/test_mcp_server.py`. Find `test_build_server_registers_thirteen_tools_including_describe_and_run` (Phase 6) and REPLACE the function with:

```python
def test_build_server_registers_fourteen_tools_including_semantic_search():
    backend = FakeBackend()
    server = bootstrap(backend, project="sample")
    tool_names = sorted(_registered_tool_names(server))
    expected = sorted([
        "find_symbol", "get_source",
        "find_callers", "find_callees",
        "runtime_only_calls", "dead_static_calls",
        "tests_for", "untested_symbols",
        "imports", "graph_status",
        "change_impact",
        "describe_schema", "run_cypher",
        "semantic_search",
    ])
    assert tool_names == expected
```

- [ ] **Step 2: Run failing test**

```bash
.venv/bin/pytest tests/unit/test_mcp_server.py -v 2>&1 | tail -8
```
Expected: failure — `semantic_search` not in registered list.

- [ ] **Step 3: Add the wrapper to `livegraph/mcp/server.py`**

Add this import near the top:

```python
from livegraph.semantic.provider import (
    EmbeddingExtraMissing, EmbeddingProvider,
)
```

Add a module-level state holder for the lazy-loaded provider, alongside `_BACKEND` and `_PROJECT`:

```python
_PROVIDER: EmbeddingProvider | None = None
```

Add a helper function (anywhere in the module body):

```python
def _get_or_load_provider() -> EmbeddingProvider | None:
    """Return the lazily-loaded LocalSTProvider, or None if extra is missing.

    First call loads the model (slow, ~3s for MiniLM). Subsequent calls
    reuse the loaded provider.
    """
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER
    try:
        from livegraph.config import load_settings
        from livegraph.semantic.provider import LocalSTProvider

        settings = load_settings()
        _PROVIDER = LocalSTProvider(
            model_name=settings.livegraph_embed_model,
            batch_size=settings.livegraph_embed_batch_size,
        )
    except EmbeddingExtraMissing:
        return None
    return _PROVIDER
```

Inside `build_server()`, after the existing `run_cypher` tool and before `return mcp`, add:

```python
    @mcp.tool()
    def semantic_search(
        query: str, limit: int = 10, kind: str = "any",
    ) -> dict[str, Any]:
        """Find code symbols by vector similarity to a natural-language query.

        - ``query``: natural-language description of the code you want to find.
        - ``limit``: top-K results (default 10).
        - ``kind``: ``"any"`` (default), ``"function"``, or ``"method"``.

        Returns ``{results, model, embedded_count, warning}``. If the
        ``[semantic]`` extra is not installed, returns an empty result list
        and a warning with the install hint.
        """
        backend, project = _require_state()
        provider = _get_or_load_provider()
        if provider is None:
            return {
                "results": [],
                "model": "unknown",
                "embedded_count": 0,
                "warning": (
                    "semantic search not enabled — install with "
                    "`pip install livegraph[semantic]`"
                ),
            }
        return tools.semantic_search(
            backend, project, provider, query=query,
            limit=limit, kind=kind,
        )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/unit/test_mcp_server.py -v 2>&1 | tail -10
```
Expected: server tests pass; tool count is 14.

- [ ] **Step 5: Update the smoke test** in `tests/integration/test_mcp_server_smoke.py`. Find the existing `sorted([...])` block and add `"semantic_search"`:

```python
        assert tool_names == sorted([
            "find_symbol", "get_source",
            "find_callers", "find_callees",
            "runtime_only_calls", "dead_static_calls",
            "tests_for", "untested_symbols",
            "imports", "graph_status",
            "change_impact",
            "describe_schema", "run_cypher",
            "semantic_search",
        ])
```

- [ ] **Step 6: Full suite verify**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 213 passed.

- [ ] **Step 7: Commit**

```bash
git add livegraph/mcp/server.py tests/unit/test_mcp_server.py tests/integration/test_mcp_server_smoke.py
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "feat: register semantic_search as 14th MCP tool with lazy provider"
```

## Report
Status, pytest outputs, commit SHA.

---

## Task 9: Integration tests (requires real Neo4j + `[semantic]` extra)

**Files:**
- Create: `tests/integration/test_semantic_integration.py`

This is the only Phase 7 task that actually installs `sentence-transformers`. The integration tests are gated on both `@pytest.mark.integration` AND `@pytest.mark.semantic`, so users without the extra installed will see them skipped.

- [ ] **Step 1: Install the `[semantic]` extra in the test venv**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
.venv/bin/pip install -e '.[semantic]' 2>&1 | tail -5
```
Expected: `Successfully installed ... sentence-transformers-X.Y.Z torch-X.Y.Z ...`. This pulls ~2 GB. If `pip` exits with errors, document them and stop; the user may need a system-level toolchain fix.

- [ ] **Step 2: Confirm `sentence_transformers` imports**

```bash
.venv/bin/python -c "import sentence_transformers; print(sentence_transformers.__version__)"
```
Expected: a version number like `3.x.x`.

- [ ] **Step 3: Register the `semantic` pytest marker** in `pyproject.toml`

Find the existing `[tool.pytest.ini_options]` section. The `markers` entry currently has `["integration: ..."]`. Update it to:

```toml
markers = [
    "integration: requires a running Neo4j (deselect with -m 'not integration')",
    "semantic: requires the [semantic] extra installed (deselect with -m 'not semantic')",
]
```

- [ ] **Step 4: Verify Neo4j is up**

```bash
(echo > /dev/tcp/localhost/7687) 2>/dev/null && echo "neo4j up" || (echo "neo4j DOWN" && brew services start neo4j && for i in $(seq 1 30); do (echo > /dev/tcp/localhost/7687) 2>/dev/null && echo "up after ${i}s" && break; sleep 1; done)
```

- [ ] **Step 5: Write the integration tests** at `tests/integration/test_semantic_integration.py`:

```python
"""End-to-end embedding + semantic_search tests against real Neo4j."""
from __future__ import annotations

import hashlib

import pytest

from livegraph.mcp import tools as mcp_tools
from livegraph.semantic.embed import INDEX_NAME, embed_project
from livegraph.semantic.provider import LocalSTProvider

pytestmark = [pytest.mark.integration, pytest.mark.semantic]


@pytest.fixture(scope="module")
def provider():
    """Module-scoped: load the MiniLM model once for all semantic tests."""
    return LocalSTProvider(model_name="all-MiniLM-L6-v2", batch_size=8)


def test_embed_project_writes_every_function_and_method(
    ingested_sample, provider,
):
    backend, project = ingested_sample
    summary = embed_project(backend, project, provider)
    assert summary.embedded >= 5     # sample has at least 5 functions/methods
    assert summary.skipped == 0

    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
        "-[:DEFINES|HAS_METHOD*1..2]->(s:Symbol) "
        "RETURN count(DISTINCT s) AS n", project=project,
    )
    assert rows[0]["n"] >= 5

    # All :Symbol nodes have the three new properties.
    rows = backend.execute(
        "MATCH (s:Symbol) "
        "WHERE s.embedding IS NULL OR s.embedding_source_hash IS NULL "
        "  OR s.embedding_model IS NULL "
        "RETURN count(s) AS n",
    )
    assert rows[0]["n"] == 0

    # The vector index exists.
    rows = backend.execute(
        "SHOW INDEXES YIELD name WHERE name = $n RETURN name",
        n=INDEX_NAME,
    )
    assert rows and rows[0]["name"] == INDEX_NAME


def test_semantic_search_finds_addition_in_calculator(
    ingested_sample, provider,
):
    """The Phase 7 acceptance test.

    'addition arithmetic' must return Calculator.add in the top-3 results.
    """
    backend, project = ingested_sample
    embed_project(backend, project, provider)

    result = mcp_tools.semantic_search(
        backend, project, provider,
        query="addition arithmetic", limit=3,
    )
    qns = [r["qualified_name"] for r in result["results"]]
    assert "calculator.py::Calculator.add" in qns
    assert result["model"] == "all-MiniLM-L6-v2"
    assert result["embedded_count"] >= 5
    assert result["warning"] is None


def test_embed_idempotent_re_run_does_nothing(ingested_sample, provider):
    backend, project = ingested_sample
    embed_project(backend, project, provider)
    second = embed_project(backend, project, provider)
    assert second.embedded == 0
    assert second.unchanged >= 5


def test_embed_re_embeds_on_source_change(ingested_sample, provider):
    backend, project = ingested_sample
    embed_project(backend, project, provider)

    # Mutate one symbol's source directly in the graph (simulating Phase 5
    # update); embed should re-embed exactly that symbol.
    backend.execute(
        "MATCH (m:Method "
        "    {qualified_name: 'calculator.py::Calculator.add'}) "
        "SET m.source = $new_source",
        new_source="def add(self, a, b):\n    return a + b + 0\n",
    )
    summary = embed_project(backend, project, provider)
    assert summary.embedded == 1


def test_rebuild_drops_index_and_re_embeds_all(ingested_sample, provider):
    backend, project = ingested_sample
    embed_project(backend, project, provider)

    rebuild_summary = embed_project(
        backend, project, provider, rebuild=True,
    )
    assert rebuild_summary.embedded >= 5
    # Index exists again after rebuild.
    rows = backend.execute(
        "SHOW INDEXES YIELD name WHERE name = $n RETURN name",
        n=INDEX_NAME,
    )
    assert rows and rows[0]["name"] == INDEX_NAME


def test_semantic_search_warns_when_no_index(ingested_sample, provider):
    """Before any embed: result is empty with a clear warning."""
    backend, project = ingested_sample
    # No embed_project call. Drop the index in case a prior test left it.
    backend.execute(f"DROP INDEX {INDEX_NAME} IF EXISTS")

    result = mcp_tools.semantic_search(
        backend, project, provider, query="anything",
    )
    assert result["results"] == []
    assert "no embeddings" in (result["warning"] or "").lower()
    assert result["embedded_count"] == 0
```

- [ ] **Step 6: Run the integration tests**

```bash
.venv/bin/pytest tests/integration/test_semantic_integration.py -v -m "integration and semantic" 2>&1 | tail -20
```
Expected: 6 passed. First run is slow (~3-30s for model download/load); subsequent runs reuse the HuggingFace cache.

If `test_semantic_search_finds_addition_in_calculator` fails (Calculator.add not in top 3), the MiniLM model's semantic neighborhood may not match "addition arithmetic" closely enough; try `query="add two numbers"`. Do NOT weaken the acceptance test by removing the assertion — pick a query that demonstrably retrieves the target.

- [ ] **Step 7: Run the full integration suite**

```bash
.venv/bin/pytest -m integration -q 2>&1 | tail -3
```
Expected: previous integration count + 6 = 38 passed.

- [ ] **Step 8: Run unit suite for no regressions**

```bash
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
```
Expected: 213 passed.

- [ ] **Step 9: Commit**

```bash
git add tests/integration/test_semantic_integration.py pyproject.toml
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "test: semantic search integration tests + semantic pytest marker"
```

## Report
Status (DONE / BLOCKED), full Step 6 output, Step 7-8 totals, commit SHA, and any debugging you needed (especially around the acceptance test query string).

---

## Task 10: README + final verify

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a new section to `README.md`** (preserve existing content, add at the end):

```markdown

## Semantic search (`livegraph embed` + `semantic_search`)

For questions where the *concept* is clear but the right *names* aren't,
livegraph can compute vector embeddings of every Function and Method
and serve them via a 14th MCP tool.

This stack is **opt-in**:

```bash
pip install 'livegraph[semantic]'    # adds sentence-transformers + torch (~2 GB)
```

After ingesting a project, compute its embeddings:

```bash
LIVEGRAPH_PROJECT=myproject livegraph embed
```

The default model is `all-MiniLM-L6-v2` (384 dimensions, ~80 MB). Override
via `LIVEGRAPH_EMBED_MODEL` (any HuggingFace sentence-transformers model id).

Re-running `livegraph embed` is idempotent — only symbols whose source has
changed since the last run get re-embedded (tracked via
`embedding_source_hash`, mirroring Phase 5's content-hash pattern). To swap
to a model with different dimensions, pass `--rebuild`.

The MCP server exposes `semantic_search`:

```
semantic_search(query: str, limit: int = 10, kind: str = "any")
```

Example agent prompt: *"Where do we handle JWT verification?"*. The agent
calls `semantic_search("JWT verification token validation")` and the top
results are ranked by cosine similarity to the query — even if no symbol
in the project literally contains those words.

If the `[semantic]` extra is not installed, the tool returns an empty
result list with a warning containing the install hint, and the rest of
livegraph keeps working.
```

- [ ] **Step 2: Final full-suite verify**

```bash
cd /Users/yvon.zhu/Documents/GitHub/livegraph
.venv/bin/pytest -m "not integration" -q 2>&1 | tail -3
.venv/bin/pytest -m integration -q 2>&1 | tail -3
.venv/bin/ruff check livegraph 2>&1 | tail -5
```
Expected: 213 unit tests pass, 38 integration tests pass, ruff clean. Fix any ruff issues introduced by Phase 7 code only (`livegraph/semantic/*`, `livegraph/mcp/tools.py`, `livegraph/mcp/server.py`, `livegraph/cli.py`, `livegraph/config.py`).

- [ ] **Step 3: Commit**

```bash
git add README.md
git -c user.name="livegraph" -c user.email="wangzhijie19950807@gmail.com" commit -m "docs: add semantic search section to README"
```

## Report
Status, exact unit total, exact integration total, ruff result, commit SHA, any ruff fixes applied.

---

## Done

After Task 10, `livegraph mcp` serves 14 tools. After running `livegraph embed`, agents can ask conceptual questions like "where do we handle JWT verification" and `semantic_search` returns the top symbols by cosine similarity. The entire ML stack stays behind the opt-in `[semantic]` extra; users who don't want it keep the lean install and the tool returns a clear install hint.

Try it manually after merging:

```bash
pip install 'livegraph[semantic]'
livegraph build /path/to/project
LIVEGRAPH_PROJECT=name livegraph embed
LIVEGRAPH_PROJECT=name livegraph mcp     # 14 tools served
```

Then in your MCP host: *"Find code that handles user authentication."* The agent calls `semantic_search`, gets ranked results, and you can ask follow-ups using the structured tools (`find_callers`, `tests_for`, `change_impact`).

Out of scope (deliberately deferred):
- Pluggable providers (OpenAI / Voyage / Ollama)
- Auto-re-embed during `livegraph build` / `livegraph update`
- Chunked per-symbol embeddings for very long functions
- Hybrid vector+structured queries combined server-side
- Embedding Class or File nodes
- Cross-project search
- A `livegraph search "natural language"` human-facing CLI subcommand
- Multi-language support
