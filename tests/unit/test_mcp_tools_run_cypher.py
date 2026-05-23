from typing import Any

import pytest

from livegraph.mcp.cypher_guard import (
    CypherSyntaxError as GuardSyntaxError,
    CypherTimeoutError, EngineWriteAttemptedError, ForbiddenKeywordError,
)
from livegraph.mcp.tools import run_cypher


class _FakeBackend:
    """Test backend driving execute_read with success or error injection."""

    def __init__(self, *, records: list[dict[str, Any]] | None = None,
                 raise_exc: Exception | None = None) -> None:
        self._records = list(records or [])
        self._raise = raise_exc
        self.calls: list[tuple[str, dict[str, Any], int]] = []

    def verify(self) -> None:
        return None

    def execute(self, cypher, **params):
        return []

    def execute_read(self, cypher: str, timeout_seconds: int = 30,
                     **params):
        self.calls.append((cypher, dict(params), timeout_seconds))
        if self._raise is not None:
            raise self._raise
        return list(self._records), {
            "available_after_ms": 1, "consumed_after_ms": 2,
            "query_type": "read",
        }

    def close(self) -> None:
        return None


def test_run_cypher_returns_rows_and_summary():
    backend = _FakeBackend(records=[{"q": "a.py::f"}])
    result = run_cypher(backend, project="sample",
                       query="MATCH (n) RETURN n LIMIT 5")
    assert result["rows"] == [{"q": "a.py::f"}]
    assert result["row_count"] == 1
    assert result["truncated"] is False
    assert result["summary"]["query_type"] == "read"


def test_run_cypher_rejects_forbidden_keyword():
    backend = _FakeBackend()
    with pytest.raises(ForbiddenKeywordError) as exc:
        run_cypher(backend, project="sample",
                   query="MATCH (n) DELETE n")
    assert exc.value.keyword == "DELETE"
    assert exc.value.query == "MATCH (n) DELETE n"
    assert backend.calls == []


def test_run_cypher_injects_project_param():
    backend = _FakeBackend()
    run_cypher(backend, project="sample",
               query="MATCH (n) RETURN n LIMIT 1")
    _q, params, _t = backend.calls[0]
    assert params["project"] == "sample"


def test_run_cypher_caller_can_override_project():
    backend = _FakeBackend()
    run_cypher(backend, project="sample",
               query="MATCH (n) RETURN n LIMIT 1",
               params={"project": "other"})
    _q, params, _t = backend.calls[0]
    assert params["project"] == "other"


def test_run_cypher_auto_appends_limit_when_missing():
    backend = _FakeBackend()
    run_cypher(backend, project="sample",
               query="MATCH (n) RETURN n", row_limit=42)
    sent_cypher, _params, _t = backend.calls[0]
    assert sent_cypher.endswith("LIMIT 42")


def test_run_cypher_preserves_caller_limit():
    backend = _FakeBackend()
    run_cypher(backend, project="sample",
               query="MATCH (n) RETURN n LIMIT 7", row_limit=42)
    sent_cypher, _params, _t = backend.calls[0]
    assert sent_cypher.endswith("LIMIT 7")


def test_run_cypher_passes_timeout_seconds_to_backend():
    backend = _FakeBackend()
    run_cypher(backend, project="sample",
               query="MATCH (n) RETURN n LIMIT 1", timeout_seconds=11)
    _q, _params, timeout = backend.calls[0]
    assert timeout == 11


def test_run_cypher_truncates_when_rows_exceed_row_limit():
    backend = _FakeBackend(records=[{"i": i} for i in range(10)])
    result = run_cypher(backend, project="sample",
                       query="MATCH (n) RETURN n LIMIT 9999",
                       row_limit=3)
    assert result["row_count"] == 3
    assert result["truncated"] is True
    assert result["rows"] == [{"i": 0}, {"i": 1}, {"i": 2}]


def test_run_cypher_propagates_engine_write_attempted():
    backend = _FakeBackend(
        raise_exc=EngineWriteAttemptedError("CREATE (n) RETURN n"),
    )
    with pytest.raises(EngineWriteAttemptedError):
        run_cypher(backend, project="sample",
                   query="MATCH (n) WHERE n.body CONTAINS '_xCREATEx_' RETURN n")


def test_run_cypher_maps_neo4j_syntax_error():
    class _FakeSyntaxError(Exception):
        message = "Invalid input 'X'"

    backend = _FakeBackend(raise_exc=_FakeSyntaxError("Invalid input 'X'"))
    with pytest.raises(GuardSyntaxError):
        run_cypher(backend, project="sample",
                   query="MATCH (n) RETURN X LIMIT 1")


def test_run_cypher_maps_timeout_error():
    class _FakeTimeoutError(Exception):
        message = "Transaction timed out"

    backend = _FakeBackend(raise_exc=_FakeTimeoutError("timed out"))
    with pytest.raises(CypherTimeoutError):
        run_cypher(backend, project="sample",
                   query="MATCH (n) RETURN n LIMIT 1",
                   timeout_seconds=1)
