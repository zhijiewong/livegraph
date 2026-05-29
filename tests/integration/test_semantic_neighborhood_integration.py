"""semantic_neighborhood end-to-end against Neo4j + MiniLM."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.semantic]


@pytest.fixture()
def embedded_sample(ingested_sample):
    """Ingest sample, then run `livegraph embed` so the vector index exists."""
    from livegraph.semantic.embed import embed_project
    from livegraph.semantic.provider import LocalSTProvider

    backend, project = ingested_sample
    provider = LocalSTProvider(model_name="all-MiniLM-L6-v2", batch_size=8)
    embed_project(backend, project, provider)
    yield backend, project, provider


def test_addition_query_returns_calc_add_with_callers_and_tests(embedded_sample):
    from livegraph.mcp.tools_neighborhood import semantic_neighborhood

    backend, project, provider = embedded_sample
    result = semantic_neighborhood(
        backend, project, provider,
        query="addition arithmetic", limit=5,
    )
    assert result["warning"] is None
    qns = [r["qualified_name"] for r in result["results"]]
    add_qns = [q for q in qns if q and q.endswith(".add")]
    assert add_qns, f"expected an `.add` symbol in top-5, got {qns}"

    add_result = next(
        r for r in result["results"]
        if r["qualified_name"] and r["qualified_name"].endswith(".add")
    )
    # Sample project's Calculator.add has both callers (driver code) and
    # tests (the sample test suite). Both should be present.
    assert add_result["callers"], "expected at least one caller for .add"
    assert add_result["tests"], "expected at least one test for .add"
    # Regression: provenance must be derived from c.static/c.runtime, not
    # a (non-existent) r.provenance property. Every caller should carry a
    # real provenance label, not the 'unknown' fallback.
    provs = {c.get("provenance") for c in add_result["callers"]}
    assert provs & {"static", "runtime", "both"}, (
        f"callers should carry real provenance labels, got {provs}"
    )


def test_include_callers_only_omits_other_fields(embedded_sample):
    from livegraph.mcp.tools_neighborhood import semantic_neighborhood

    backend, project, provider = embedded_sample
    result = semantic_neighborhood(
        backend, project, provider,
        query="addition arithmetic", limit=3, include=["callers"],
    )
    for r in result["results"]:
        assert "callers" in r
        assert "callees" not in r
        assert "tests" not in r
