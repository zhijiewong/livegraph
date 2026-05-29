from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from livegraph.cli import app

runner = CliRunner()


def _write_cfg(tmp_path: Path, text: str = '[project]\nname = "p"\n') -> Path:
    p = tmp_path / ".livegraph.toml"
    p.write_text(text)
    return p


def test_check_help():
    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.stdout
    assert "--format" in result.stdout
    assert "--strict" in result.stdout
    assert "--fail-fast" in result.stdout


def test_check_missing_config_exits_2(tmp_path):
    result = runner.invoke(
        app, ["check", "--config", str(tmp_path / "nope.toml")],
    )
    assert result.exit_code == 2


def test_check_invalid_format_exits_2(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    result = runner.invoke(
        app, ["check", "--config", str(cfg), "--format", "yaml"],
    )
    assert result.exit_code == 2


def test_check_all_disabled_returns_zero(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    from livegraph import cli as cli_mod
    from livegraph.check.models import StalenessReport

    def fake_make_backend():
        b = type("B", (), {})()
        b.verify = lambda: None
        b.close = lambda: None
        return b

    monkeypatch.setattr(cli_mod, "_make_backend", fake_make_backend)
    monkeypatch.setattr(
        "livegraph.check.staleness.probe_staleness",
        lambda *a, **kw: StalenessReport(drifted_files=0),
    )
    result = runner.invoke(
        app, ["check", "--config", str(cfg), str(tmp_path)],
    )
    assert result.exit_code == 0
