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
    row = rows[0]
    # Real Neo4j response: row has an "options" dict with nested indexConfig.
    options = row.get("options") or {}
    config = options.get("indexConfig", options) or {}
    dims = (
        config.get("vector.dimensions")
        or options.get("dimensions")
        or row.get("dimensions")  # flat form returned by test mocks
    )
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

    to_embed: list[tuple[str, str, str]] = []
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
