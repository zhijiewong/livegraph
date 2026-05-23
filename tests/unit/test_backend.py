from livegraph.graph.backend import GraphBackend, FakeBackend


def test_fake_backend_records_calls():
    backend: GraphBackend = FakeBackend()
    backend.execute("MERGE (n:Test {id: $id})", id="x")
    assert backend.calls == [("MERGE (n:Test {id: $id})", {"id": "x"})]


def test_fake_backend_returns_canned_rows():
    backend = FakeBackend(rows=[{"count": 3}])
    assert backend.execute("MATCH (n) RETURN count(n) AS count") == [{"count": 3}]
