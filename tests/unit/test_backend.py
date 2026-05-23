from livegraph.graph.backend import GraphBackend, FakeBackend


def test_fake_backend_records_calls():
    backend: GraphBackend = FakeBackend()
    backend.execute("MERGE (n:Test {id: $id})", id="x")
    assert backend.calls == [("MERGE (n:Test {id: $id})", {"id": "x"})]


def test_fake_backend_returns_canned_rows():
    backend = FakeBackend(rows=[{"count": 3}])
    assert backend.execute("MATCH (n) RETURN count(n) AS count") == [{"count": 3}]


def test_fake_backend_execute_read_returns_rows_and_summary():
    from livegraph.graph.backend import FakeBackend
    backend = FakeBackend(rows=[{"qualified_name": "a.py::f"}])
    records, summary = backend.execute_read(
        "MATCH (n) RETURN n", project="sample",
    )
    assert records == [{"qualified_name": "a.py::f"}]
    assert summary["query_type"] == "read"
    assert "available_after_ms" in summary
    assert "consumed_after_ms" in summary


def test_fake_backend_execute_read_records_call():
    from livegraph.graph.backend import FakeBackend
    backend = FakeBackend()
    backend.execute_read("MATCH (n) RETURN n", timeout_seconds=10,
                         project="sample")
    cypher, params = backend.calls[0]
    assert cypher == "MATCH (n) RETURN n"
    # timeout_seconds is NOT a Cypher parameter — it controls the transaction.
    assert "timeout_seconds" not in params
    assert params == {"project": "sample"}


def test_fake_backend_execute_read_default_timeout():
    from livegraph.graph.backend import FakeBackend
    backend = FakeBackend()
    records, _summary = backend.execute_read("MATCH (n) RETURN n")
    assert records == []
