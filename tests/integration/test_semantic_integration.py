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
    assert summary.embedded >= 5
    assert summary.skipped == 0

    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
        "-[:DEFINES|HAS_METHOD*1..2]->(s:Symbol) "
        "RETURN count(DISTINCT s) AS n", project=project,
    )
    assert rows[0]["n"] >= 5

    rows = backend.execute(
        "MATCH (s:Symbol) "
        "WHERE s.embedding IS NULL OR s.embedding_source_hash IS NULL "
        "  OR s.embedding_model IS NULL "
        "RETURN count(s) AS n",
    )
    assert rows[0]["n"] == 0

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
    rows = backend.execute(
        "SHOW INDEXES YIELD name WHERE name = $n RETURN name",
        n=INDEX_NAME,
    )
    assert rows and rows[0]["name"] == INDEX_NAME


def test_semantic_search_warns_when_no_index(ingested_sample, provider):
    backend, project = ingested_sample
    backend.execute(f"DROP INDEX {INDEX_NAME} IF EXISTS")

    result = mcp_tools.semantic_search(
        backend, project, provider, query="anything",
    )
    assert result["results"] == []
    assert "no embeddings" in (result["warning"] or "").lower()
    assert result["embedded_count"] == 0
