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


def test_build_runs_both_phases(tmp_path, monkeypatch):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setattr(
        cli, "run_pytest",
        lambda path, python=None: {
            "root": str(tmp_path), "runtime_calls": [], "tests": [],
            "coverage": [],
        },
    )
    result = runner.invoke(cli.app, ["build", str(tmp_path)])
    assert result.exit_code == 0
    assert "Phase 1 complete" in result.stdout
    assert "Phase 2 complete" in result.stdout


def test_mcp_command_errors_when_project_missing(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    result = runner.invoke(cli.app, ["mcp"])
    assert result.exit_code != 0
    assert "LIVEGRAPH_PROJECT" in (result.output + (result.stderr or ""))


def test_mcp_command_invokes_run_stdio_with_project(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)

    captured: dict = {}

    def fake_run_stdio(b, project):
        captured["backend"] = b
        captured["project"] = project

    monkeypatch.setattr("livegraph.cli.run_stdio", fake_run_stdio)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "sample")
    result = runner.invoke(cli.app, ["mcp"])
    assert result.exit_code == 0
    assert captured["backend"] is backend
    assert captured["project"] == "sample"


def test_mcp_command_project_flag_overrides_env(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)

    captured: dict = {}

    def fake_run_stdio(b, project):
        captured["project"] = project

    monkeypatch.setattr("livegraph.cli.run_stdio", fake_run_stdio)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "fromenv")
    result = runner.invoke(cli.app, ["mcp", "--project", "fromflag"])
    assert result.exit_code == 0
    assert captured["project"] == "fromflag"
