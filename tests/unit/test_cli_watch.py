from __future__ import annotations

from typer.testing import CliRunner

from livegraph.cli import app

runner = CliRunner()


def test_watch_help():
    result = runner.invoke(app, ["watch", "--help"])
    assert result.exit_code == 0
    assert "--project" in result.stdout
    assert "--embed" in result.stdout
    assert "--debounce-ms" in result.stdout
    assert "--ignore" in result.stdout


def test_watch_requires_project(monkeypatch, tmp_path):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    result = runner.invoke(app, ["watch", str(tmp_path)])
    assert result.exit_code == 2


def test_watch_embed_without_extra_exits_1(monkeypatch, tmp_path):
    from livegraph import cli as cli_mod

    def fake_make_backend():
        b = type("B", (), {})()
        b.verify = lambda: None
        b.close = lambda: None
        return b

    monkeypatch.setattr(cli_mod, "_make_backend", fake_make_backend)
    monkeypatch.setattr(cli_mod, "_resolve_root_path",
                        lambda *a, **kw: str(tmp_path))

    from livegraph.semantic.provider import EmbeddingExtraMissing

    def boom(_settings):
        raise EmbeddingExtraMissing("missing extra")

    monkeypatch.setattr(cli_mod, "_make_embedding_provider", boom)

    result = runner.invoke(
        app, ["watch", "--project", "p", "--embed", str(tmp_path)],
    )
    assert result.exit_code == 1
    out = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "semantic" in out.lower() or "extra" in out.lower()
