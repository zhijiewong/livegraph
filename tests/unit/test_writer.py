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
        {"path": "a.py", "name": "a.py", "language": "python", "parse_error": False,
         "content_hash": None}
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


def test_write_files_includes_content_hash_in_row():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    from livegraph.models import FileRecord

    backend = FakeBackend()
    writer = GraphWriter(backend, batch_size=100)
    writer.write_files(
        "proj",
        [FileRecord(path="a.py", name="a.py", content_hash="deadbeef")],
        root_path="/tmp/proj",
    )
    query, params = backend.calls[0]
    assert "content_hash" in query
    assert params["rows"][0]["content_hash"] == "deadbeef"
    assert params["root_path"] == "/tmp/proj"
    assert "root_path" in query


def test_write_files_root_path_is_optional():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    from livegraph.models import FileRecord

    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).write_files(
        "proj", [FileRecord(path="a.py", name="a.py")]
    )
    _query, params = backend.calls[0]
    assert params.get("root_path") is None


def test_delete_symbols_issues_detach_delete_with_qns():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).delete_symbols(
        ["a.py::f", "a.py::g"])
    query, params = backend.calls[0]
    assert "DETACH DELETE" in query and "UNWIND" in query
    assert params["qns"] == ["a.py::f", "a.py::g"]


def test_delete_symbols_no_op_on_empty():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).delete_symbols([])
    assert backend.calls == []


def test_delete_outgoing_calls_for_file_issues_match_delete():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).delete_outgoing_calls_for_file("a.py")
    query, params = backend.calls[0]
    assert "[c:CALLS]" in query and "DELETE c" in query
    assert "SET c.static = false" in query
    assert params["file"] == "a.py"


def test_delete_imports_from_file_issues_match_delete():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).delete_imports_from_file("a.py")
    query, params = backend.calls[0]
    assert "[r:IMPORTS]" in query and "DELETE r" in query
    assert params["file"] == "a.py"


def test_flag_runtime_stale_for_file():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).flag_runtime_stale_for_file(
        project="proj", file="a.py")
    query, params = backend.calls[0]
    assert "SET s.runtime_stale = true" in query
    assert ":Project {name: $project}" in query
    assert params["project"] == "proj"
    assert params["file"] == "a.py"


def test_clear_runtime_stale_for_symbols():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).clear_runtime_stale_for_symbols(
        ["a.py::f", "a.py::g"])
    query, params = backend.calls[0]
    assert "SET s.runtime_stale = false" in query
    assert params["qns"] == ["a.py::f", "a.py::g"]


def test_clear_runtime_stale_no_op_on_empty():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).clear_runtime_stale_for_symbols([])
    assert backend.calls == []


def test_delete_file_completely():
    from livegraph.graph.backend import FakeBackend
    from livegraph.graph.writer import GraphWriter
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).delete_file(
        project="proj", file="a.py")
    query, params = backend.calls[0]
    assert "DETACH DELETE" in query
    assert "{path: $file}" in query
    assert params["project"] == "proj"
    assert params["file"] == "a.py"
