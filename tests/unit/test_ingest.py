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
