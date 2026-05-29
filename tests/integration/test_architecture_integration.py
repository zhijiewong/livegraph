"""End-to-end: real Neo4j + architecture analysis tools."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def synthetic_arch(neo4j_backend):
    """Build a tiny synthetic graph with known cycles and a layering
    violation. We bypass the parser and write directly via Cypher."""
    backend = neo4j_backend
    project = "arch_test"

    # Project + 3 files
    backend.execute(
        "MERGE (p:Project {name: $project}) "
        "WITH p UNWIND $paths AS path "
        "MERGE (f:File {path: path}) "
        "MERGE (p)-[:CONTAINS]->(f)",
        project=project,
        paths=["web/handlers.py", "domain/calc.py", "infra/db.py"],
    )
    # File-level IMPORTS forming a 2-cycle: web <-> domain
    backend.execute(
        "MATCH (a:File {path: 'web/handlers.py'}), "
        "      (b:File {path: 'domain/calc.py'}) "
        "MERGE (a)-[:IMPORTS]->(b) "
        "MERGE (b)-[:IMPORTS]->(a)",
    )
    # Symbols
    backend.execute(
        "MATCH (f:File {path: 'web/handlers.py'}) "
        "MERGE (s1:Function:Symbol {qualified_name: 'web.handlers.foo', "
        "                  name: 'foo', file: 'web/handlers.py', "
        "                  start_line: 1, end_line: 3}) "
        "MERGE (s2:Function:Symbol {qualified_name: 'web.handlers.bar', "
        "                  name: 'bar', file: 'web/handlers.py', "
        "                  start_line: 4, end_line: 6}) "
        "MERGE (f)-[:DEFINES]->(s1) "
        "MERGE (f)-[:DEFINES]->(s2)",
    )
    # Call cycle s1 <-> s2 (both static)
    backend.execute(
        "MATCH (s1 {qualified_name: 'web.handlers.foo'}), "
        "      (s2 {qualified_name: 'web.handlers.bar'}) "
        "MERGE (s1)-[:CALLS {static: true, runtime: false}]->(s2) "
        "MERGE (s2)-[:CALLS {static: true, runtime: false}]->(s1)",
    )
    return backend, project


def test_find_cycles_module_scope_finds_the_2_cycle(synthetic_arch):
    from livegraph.mcp.tools_architecture import find_cycles

    backend, project = synthetic_arch
    out = find_cycles(backend, project, scope="module")
    assert out["warning"] is None
    assert any(
        sorted(c["nodes"]) == ["domain/calc.py", "web/handlers.py"]
        for c in out["cycles"]
    )


def test_find_cycles_call_scope_finds_foo_bar_cycle(synthetic_arch):
    from livegraph.mcp.tools_architecture import find_cycles

    backend, project = synthetic_arch
    out = find_cycles(backend, project, scope="call")
    assert any(
        sorted(c["nodes"]) == ["web.handlers.bar", "web.handlers.foo"]
        for c in out["cycles"]
    )


def test_layering_violations_finds_domain_to_web(synthetic_arch):
    from livegraph.mcp.tools_architecture import layering_violations

    backend, project = synthetic_arch
    out = layering_violations(
        backend, project,
        layers=[
            {"name": "web", "patterns": ["web/**"]},
            {"name": "domain", "patterns": ["domain/**"]},
            {"name": "infra", "patterns": ["infra/**"]},
        ],
    )
    # domain/calc.py -> web/handlers.py is the upward edge.
    assert any(
        v["from_layer"] == "domain" and v["to_layer"] == "web"
        for v in out["violations"]
    )
    assert out["summary"]["files_unlayered"] == 0


def test_hubs_returns_foo_and_bar_at_min_fanin_1(synthetic_arch):
    from livegraph.mcp.tools_architecture import hubs

    backend, project = synthetic_arch
    out = hubs(backend, project, min_fanin=1)
    qns = {r["qualified_name"] for r in out["results"]}
    assert "web.handlers.foo" in qns or "web.handlers.bar" in qns
