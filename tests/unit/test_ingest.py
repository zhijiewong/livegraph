from livegraph.graph.backend import FakeBackend
from livegraph.ingest import ingest_project


def test_ingest_writes_files_definitions_and_calls(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def total(xs):\n    return add(xs[0], xs[1])\n"
    )
    backend = FakeBackend()
    summary = ingest_project(str(tmp_path), backend, project_name="demo",
                             batch_size=100)
    assert summary.files == 1
    assert summary.definitions == 2
    assert summary.call_edges == 1
    assert summary.parse_errors == 0
    issued = " ".join(q for q, _ in backend.calls)
    assert "CONSTRAINT" in issued and ":Function" in issued and ":CALLS" in issued


def test_ingest_records_parse_errors_without_aborting(tmp_path):
    (tmp_path / "ok.py").write_text("def f():\n    return 1\n")
    (tmp_path / "bad.py").write_text("def f(:\n")
    backend = FakeBackend()
    summary = ingest_project(str(tmp_path), backend, project_name="demo",
                             batch_size=100)
    assert summary.files == 2
    assert summary.parse_errors == 1
    assert summary.definitions == 1


def test_ingest_stores_content_hashes_and_root_path(tmp_path):
    import hashlib
    import os
    from livegraph.graph.backend import FakeBackend
    from livegraph.ingest import ingest_project

    src = "def f():\n    return 1\n"
    (tmp_path / "m.py").write_text(src)
    expected_hash = hashlib.sha256(src.encode()).hexdigest()

    backend = FakeBackend()
    ingest_project(str(tmp_path), backend, project_name="demo",
                   batch_size=100)
    write_files_calls = [c for c in backend.calls if "MERGE (p:Project" in c[0]]
    assert write_files_calls, "expected a write_files call"
    _query, params = write_files_calls[0]
    rows = params["rows"]
    by_path = {row["path"]: row for row in rows}
    assert by_path["m.py"]["content_hash"] == expected_hash
    assert params["root_path"] == os.path.abspath(str(tmp_path))
