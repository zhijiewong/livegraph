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


@pytest.fixture()
def ingested_sample(neo4j_backend, sample_project_path):
    """Run Phase 1 + Phase 2 on the sample project; yield ``(backend, project_name)``."""
    import sys

    from livegraph.augment import augment_from_observations
    from livegraph.ingest import ingest_project
    from livegraph.runtime.runner import run_pytest

    project_name = "sample"
    ingest_project(sample_project_path, neo4j_backend,
                   project_name=project_name, batch_size=100)
    observations = run_pytest(sample_project_path, python=sys.executable)
    augment_from_observations(observations, neo4j_backend, batch_size=100)
    yield neo4j_backend, project_name
