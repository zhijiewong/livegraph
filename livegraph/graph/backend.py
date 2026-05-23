"""Swappable graph-database backend.

All database access goes through ``GraphBackend``. v1 ships ``Neo4jBackend``;
``FakeBackend`` exists for unit tests. Swapping the backend later (e.g. to an
embedded database) means adding one class here and nothing else changes.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GraphBackend(Protocol):
    """Minimal interface every graph backend must provide."""

    def verify(self) -> None:
        """Raise if the database is unreachable."""

    def execute(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """Run a Cypher query and return rows as plain dicts."""

    def close(self) -> None:
        """Release all resources."""


class Neo4jBackend:
    """``GraphBackend`` backed by a Neo4j Bolt connection."""

    def __init__(self, uri: str, user: str, password: str,
                 database: str = "neo4j") -> None:
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    def verify(self) -> None:
        from neo4j.exceptions import Neo4jError, ServiceUnavailable

        try:
            self._driver.verify_connectivity()
        except (ServiceUnavailable, Neo4jError) as exc:  # pragma: no cover
            raise ConnectionError(f"Neo4j unreachable: {exc}") from exc

    def execute(self, query: str, **params: Any) -> list[dict[str, Any]]:
        records, _summary, _keys = self._driver.execute_query(
            query, database_=self._database, **params,
        )
        return [record.data() for record in records]

    def close(self) -> None:
        self._driver.close()


class FakeBackend:
    """In-memory ``GraphBackend`` for unit tests.

    Records every ``execute`` call and returns canned rows.
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rows = rows or []

    def verify(self) -> None:
        return None

    def execute(self, query: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        return list(self._rows)

    def close(self) -> None:
        return None
