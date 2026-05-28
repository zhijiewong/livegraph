# tests/unit/test_config.py
from livegraph.config import Settings


def test_defaults_when_no_env(monkeypatch):
    for var in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
                "LIVEGRAPH_BATCH_SIZE", "LIVEGRAPH_LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.neo4j_uri == "bolt://localhost:7687"
    assert settings.neo4j_user == "neo4j"
    assert settings.livegraph_batch_size == 1000


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://example:9999")
    monkeypatch.setenv("LIVEGRAPH_BATCH_SIZE", "50")
    settings = Settings(_env_file=None)
    assert settings.neo4j_uri == "bolt://example:9999"
    assert settings.livegraph_batch_size == 50


def test_livegraph_project_defaults_to_none(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_PROJECT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.livegraph_project is None


def test_livegraph_project_from_env(monkeypatch):
    monkeypatch.setenv("LIVEGRAPH_PROJECT", "myproject")
    settings = Settings(_env_file=None)
    assert settings.livegraph_project == "myproject"


def test_query_row_limit_default(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_QUERY_ROW_LIMIT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.livegraph_query_row_limit == 1000


def test_query_row_limit_from_env(monkeypatch):
    monkeypatch.setenv("LIVEGRAPH_QUERY_ROW_LIMIT", "250")
    settings = Settings(_env_file=None)
    assert settings.livegraph_query_row_limit == 250


def test_query_timeout_default(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_QUERY_TIMEOUT_SECONDS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.livegraph_query_timeout_seconds == 30


def test_query_timeout_from_env(monkeypatch):
    monkeypatch.setenv("LIVEGRAPH_QUERY_TIMEOUT_SECONDS", "5")
    settings = Settings(_env_file=None)
    assert settings.livegraph_query_timeout_seconds == 5


def test_embed_model_default(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_EMBED_MODEL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.livegraph_embed_model == "all-MiniLM-L6-v2"


def test_embed_model_from_env(monkeypatch):
    monkeypatch.setenv("LIVEGRAPH_EMBED_MODEL", "microsoft/unixcoder-base")
    settings = Settings(_env_file=None)
    assert settings.livegraph_embed_model == "microsoft/unixcoder-base"


def test_embed_batch_size_default(monkeypatch):
    monkeypatch.delenv("LIVEGRAPH_EMBED_BATCH_SIZE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.livegraph_embed_batch_size == 32


def test_embed_batch_size_from_env(monkeypatch):
    monkeypatch.setenv("LIVEGRAPH_EMBED_BATCH_SIZE", "64")
    settings = Settings(_env_file=None)
    assert settings.livegraph_embed_batch_size == 64


def test_settings_default_watch_debounce_ms():
    from livegraph.config import Settings
    s = Settings()
    assert s.livegraph_watch_debounce_ms == 300
