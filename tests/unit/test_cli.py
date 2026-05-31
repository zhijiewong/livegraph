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

    def fake_run_stdio(b, project, **kwargs):
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

    def fake_run_stdio(b, project, **kwargs):
        captured["project"] = project

    monkeypatch.setattr("livegraph.cli.run_stdio", fake_run_stdio)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "fromenv")
    result = runner.invoke(cli.app, ["mcp", "--project", "fromflag"])
    assert result.exit_code == 0
    assert captured["project"] == "fromflag"


def test_update_command_errors_when_project_missing(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    result = runner.invoke(cli.app, ["update"])
    assert result.exit_code != 0
    assert "LIVEGRAPH_PROJECT" in (result.output + (result.stderr or ""))


def test_update_command_dry_run_does_not_call_reingest(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    backend = FakeBackend(rows=[])
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "p")

    called: dict = {"reingest": False}

    def fake_reingest(*args, **kwargs):
        called["reingest"] = True

    monkeypatch.setattr("livegraph.cli.reingest_files", fake_reingest)
    result = runner.invoke(cli.app, ["update", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0
    assert called["reingest"] is False
    assert "added" in result.stdout.lower() or "changed" in result.stdout.lower()


def test_update_command_invokes_reingest_with_changeset(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    backend = FakeBackend(rows=[])
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "p")

    captured: dict = {}

    def fake_reingest(root, backend_arg, project, changeset, batch_size=1000):
        captured["root"] = root
        captured["project"] = project
        captured["changeset"] = changeset
        from livegraph.incremental import UpdateSummary
        return UpdateSummary(added=1, changed=0, deleted=0, unchanged=0,
                             parse_errors=0)

    monkeypatch.setattr("livegraph.cli.reingest_files", fake_reingest)
    result = runner.invoke(cli.app, ["update", str(tmp_path)])
    assert result.exit_code == 0
    assert captured["project"] == "p"
    assert "a.py" in captured["changeset"].added


def test_embed_command_errors_when_project_missing(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    result = runner.invoke(cli.app, ["embed"])
    assert result.exit_code != 0
    assert "LIVEGRAPH_PROJECT" in (result.output + (result.stderr or ""))


def test_embed_command_handles_missing_extra(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "p")

    from livegraph.semantic.provider import EmbeddingExtraMissing

    def fake_make_provider(*args, **kwargs):
        raise EmbeddingExtraMissing(
            "sentence-transformers is not installed. Install the optional "
            "extra: pip install 'livegraph[semantic]'"
        )

    monkeypatch.setattr("livegraph.cli._make_embedding_provider",
                       fake_make_provider)
    result = runner.invoke(cli.app, ["embed"])
    assert result.exit_code == 1
    assert "livegraph[semantic]" in (result.output + (result.stderr or ""))


def test_embed_command_invokes_embed_project(monkeypatch, tmp_path):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "sample")

    class _FakeProvider:
        name = "mock-model"
        dimensions = 384
        batch_size = 32

    monkeypatch.setattr("livegraph.cli._make_embedding_provider",
                       lambda settings: _FakeProvider())

    captured: dict = {}
    def fake_embed(backend_arg, project, provider, rebuild=False):
        captured["project"] = project
        captured["rebuild"] = rebuild
        captured["provider_name"] = provider.name
        from livegraph.semantic.embed import EmbedSummary
        return EmbedSummary(embedded=3, unchanged=2, skipped=0)

    monkeypatch.setattr("livegraph.cli.embed_project", fake_embed)
    result = runner.invoke(cli.app, ["embed"])
    assert result.exit_code == 0
    assert captured["project"] == "sample"
    assert captured["rebuild"] is False
    assert captured["provider_name"] == "mock-model"
    assert "3" in result.output and "2" in result.output


def test_embed_command_rebuild_flag(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda: backend)
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "p")

    class _FakeProvider:
        name = "m"; dimensions = 384; batch_size = 32

    monkeypatch.setattr("livegraph.cli._make_embedding_provider",
                       lambda settings: _FakeProvider())

    captured: dict = {}
    def fake_embed(*args, rebuild=False, **kwargs):
        captured["rebuild"] = rebuild
        from livegraph.semantic.embed import EmbedSummary
        return EmbedSummary(0, 0, 0)

    monkeypatch.setattr("livegraph.cli.embed_project", fake_embed)
    result = runner.invoke(cli.app, ["embed", "--rebuild"])
    assert result.exit_code == 0
    assert captured["rebuild"] is True


def test_build_help_mentions_lang():
    result = runner.invoke(cli.app, ["build", "--help"])
    assert result.exit_code == 0
    assert "--lang" in result.stdout


def test_build_invalid_lang_exits_2(tmp_path):
    result = runner.invoke(cli.app, ["build", str(tmp_path), "--lang", "rust"])
    assert result.exit_code == 2
