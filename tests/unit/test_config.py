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
