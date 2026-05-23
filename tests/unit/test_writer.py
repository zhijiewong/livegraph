from livegraph.graph.backend import FakeBackend
from livegraph.graph.writer import GraphWriter
from livegraph.models import FileRecord, Definition, CallEdge


def test_write_files_batches_by_size():
    backend = FakeBackend()
    writer = GraphWriter(backend, batch_size=2)
    files = [FileRecord(path=f"f{i}.py", name=f"f{i}.py") for i in range(5)]
    writer.write_files("proj", files)
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


def test_write_calls_preserves_runtime_provenance_on_reingest():
    """Phase 1 re-running must not reset c.runtime to false on edges that
    Phase 2 has marked runtime=true. Achieved via coalesce()."""
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).write_calls(
        [CallEdge("a.py::f", "a.py::g", static=True, runtime=False)]
    )
    query, _params = backend.calls[0]
    assert "coalesce(c.runtime" in query
    assert "coalesce(" in query and "c.observed_count" in query
