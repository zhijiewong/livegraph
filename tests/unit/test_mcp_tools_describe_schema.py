from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import describe_schema


def test_describe_schema_returns_documented_top_level_keys():
    backend = FakeBackend()
    result = describe_schema(backend, project="sample")
    assert result["project"] == "sample"
    assert "neo4j_version" in result
    assert "node_labels" in result
    assert "edge_types" in result
    assert "safety" in result
    assert "example_queries" in result


def test_describe_schema_node_labels_cover_every_kind():
    backend = FakeBackend()
    result = describe_schema(backend, project="sample")
    labels = set(result["node_labels"])
    expected = {"Project", "File", "Class", "Function", "Method",
                "Test", "Module"}
    assert expected <= labels


def test_describe_schema_edge_types_cover_every_relation():
    backend = FakeBackend()
    result = describe_schema(backend, project="sample")
    edges = set(result["edge_types"])
    expected = {"CONTAINS", "DEFINES", "HAS_METHOD",
                "IMPORTS", "CALLS", "COVERS"}
    assert expected <= edges


def test_describe_schema_safety_advertises_read_only():
    backend = FakeBackend()
    result = describe_schema(backend, project="sample")
    safety = result["safety"]
    assert safety["read_only"] is True
    assert "CREATE" in safety["forbidden_keywords"]
    assert "CALL" in safety["forbidden_keywords"]
    assert safety["row_limit_default"] == 1000
    assert safety["timeout_seconds_default"] == 30
    assert safety["project_auto_injected"] is True
    assert "$project" in safety["convention"]


def test_describe_schema_examples_cover_six_intents():
    backend = FakeBackend()
    result = describe_schema(backend, project="sample")
    intents = [ex["intent"] for ex in result["example_queries"]]
    assert len(intents) == 6
    assert any("Dynamic-dispatch" in i for i in intents)
    for ex in result["example_queries"]:
        assert "query" in ex and isinstance(ex["query"], str)
        assert "params_hint" in ex and isinstance(ex["params_hint"], dict)


def test_describe_schema_does_not_call_backend():
    """describe_schema is static; it must not touch the backend."""
    backend = FakeBackend()
    describe_schema(backend, project="sample")
    assert backend.calls == []
