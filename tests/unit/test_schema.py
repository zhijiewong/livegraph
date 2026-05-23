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
