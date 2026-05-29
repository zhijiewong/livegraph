from __future__ import annotations

from typer.testing import CliRunner

from livegraph.cli import app

runner = CliRunner()


def test_ingest_history_help():
    result = runner.invoke(app, ["ingest-history", "--help"])
    assert result.exit_code == 0
    assert "--project" in result.stdout
    assert "--since-last" in result.stdout
    assert "--max-commits" in result.stdout


def test_ingest_history_requires_project(monkeypatch, tmp_path):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    result = runner.invoke(app, ["ingest-history", str(tmp_path)])
    assert result.exit_code == 2


def test_ingest_history_exits_2_on_non_git_dir(monkeypatch, tmp_path):
    from livegraph import cli as cli_mod

    def fake_make_backend():
        b = type("B", (), {})()
        b.verify = lambda: None
        b.close = lambda: None
        return b

    monkeypatch.setattr(cli_mod, "_make_backend", fake_make_backend)
    monkeypatch.setattr(cli_mod, "_resolve_root_path",
                        lambda *a, **kw: str(tmp_path))
    result = runner.invoke(
        app, ["ingest-history", "--project", "p", str(tmp_path)],
    )
    assert result.exit_code == 2
    out = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "git" in out.lower()
