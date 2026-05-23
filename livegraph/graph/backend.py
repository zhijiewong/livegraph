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

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Run a Cypher query and return rows as plain dicts."""

    def execute_read(
        self, cypher: str, timeout_seconds: int = 30,
        **params: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Run a Cypher query in a READ transaction.

        Returns ``(records, summary)``. ``summary`` is a dict with
        ``available_after_ms``, ``consumed_after_ms``, and ``query_type``.
        Engine-enforced read mode: any write clause that bypassed lexical
        scanning is rejected here.
        """

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

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        records, _summary, _keys = self._driver.execute_query(
            cypher, database_=self._database, **params,
        )
        return [record.data() for record in records]

    def execute_read(
        self, cypher: str, timeout_seconds: int = 30,
        **params: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # Neo4j's session.execute_read treats timeout=0 as "no timeout" in some
        # driver versions and as "immediate" in others. We require an explicit
        # positive value; callers that want unlimited execution should pass
        # a generous value (e.g., 600) rather than 0.
        effective_timeout = max(1, int(timeout_seconds))
        timeout_s: float = float(effective_timeout)

        def _work(tx: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            result = tx.run(cypher, **params)
            records = [record.data() for record in result]
            consumed = result.consume()
            summary = {
                "available_after_ms": consumed.result_available_after or 0,
                "consumed_after_ms": consumed.result_consumed_after or 0,
                "query_type": "read",
            }
            return records, summary

        _work.timeout = timeout_s  # type: ignore[attr-defined]

        with self._driver.session(
            database=self._database, default_access_mode="READ",
        ) as session:
            return session.execute_read(_work)

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

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        return list(self._rows)

    def execute_read(
        self, cypher: str, timeout_seconds: int = 30,
        **params: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        self.calls.append((cypher, params))
        return list(self._rows), {
            "available_after_ms": 0,
            "consumed_after_ms": 0,
            "query_type": "read",
        }

    def close(self) -> None:
        return None
