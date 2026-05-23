"""Shared fixtures for integration tests (require a running Neo4j)."""
from __future__ import annotations

import os

import pytest

from livegraph.config import load_settings
from livegraph.graph.backend import Neo4jBackend

SAMPLE_PROJECT = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "sample_project")


@pytest.fixture()
def neo4j_backend():
    """Yield a Neo4jBackend, wiping the graph before and after the test."""
    settings = load_settings()
    backend = Neo4jBackend(settings.neo4j_uri, settings.neo4j_user,
                           settings.neo4j_password)
    try:
        backend.verify()
    except ConnectionError:
        pytest.skip("Neo4j not reachable; run `docker compose up -d`.")
    backend.execute("MATCH (n) DETACH DELETE n")
    yield backend
    backend.execute("MATCH (n) DETACH DELETE n")
    backend.close()


@pytest.fixture()
def sample_project_path() -> str:
    """Absolute path to the fixture sample project."""
    return os.path.abspath(SAMPLE_PROJECT)
