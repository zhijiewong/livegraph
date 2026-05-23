"""Typed configuration loaded from the environment / a .env file."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for livegraph.

    Field names map case-insensitively to the uppercase env vars
    (e.g. ``neo4j_uri`` <- ``NEO4J_URI``).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "livegraph-local"
    livegraph_batch_size: int = 1000
    livegraph_log_level: str = "INFO"
    livegraph_project: str | None = None
    livegraph_query_row_limit: int = 1000
    livegraph_query_timeout_seconds: int = 30


def load_settings() -> Settings:
    """Return a Settings instance built from the environment and .env."""
    return Settings()
