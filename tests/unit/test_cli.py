from typer.testing import CliRunner

import livegraph.cli as cli
from livegraph.graph.backend import FakeBackend

runner = CliRunner()


def test_ingest_command_invokes_ingestion(tmp_path, monkeypatch):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    result = runner.invoke(cli.app, ["ingest", str(tmp_path)])
    assert result.exit_code == 0
    assert "files" in result.stdout.lower()


def test_status_command_reports_counts(monkeypatch):
    backend = FakeBackend(rows=[{"label": "File", "n": 7}])
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "File" in result.stdout


def test_clean_command_runs_detach_delete(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    result = runner.invoke(cli.app, ["clean", "--yes"])
    assert result.exit_code == 0
    assert any("DETACH DELETE" in q for q, _ in backend.calls)
