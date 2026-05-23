"""Graph schema: label/edge constants and constraint setup."""
from __future__ import annotations

from livegraph.graph.backend import GraphBackend

# Node labels.
PROJECT = "Project"
FILE = "File"
MODULE = "Module"
CLASS = "Class"
FUNCTION = "Function"
METHOD = "Method"
TEST = "Test"          # an extra label added to Function nodes in Phase 2

# Edge types.
CONTAINS = "CONTAINS"
DEFINES = "DEFINES"
HAS_METHOD = "HAS_METHOD"
IMPORTS = "IMPORTS"
CALLS = "CALLS"
COVERS = "COVERS"

# The unique key property for every keyed node label.
NODE_KEYS: dict[str, str] = {
    PROJECT: "name",
    FILE: "path",
    MODULE: "name",
    CLASS: "qualified_name",
    FUNCTION: "qualified_name",
    METHOD: "qualified_name",
}


def create_schema(backend: GraphBackend) -> None:
    """Create one uniqueness constraint per keyed label (idempotent).

    A Neo4j uniqueness constraint auto-creates a backing index, so this
    also makes MERGE on the key fast.
    """
    for label, key in NODE_KEYS.items():
        constraint = f"livegraph_{label.lower()}_{key}"
        backend.execute(
            f"CREATE CONSTRAINT {constraint} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
        )
