import pytest

from livegraph.mcp.cypher_guard import (
    forbidden_keyword, auto_limit, inject_project,
    ForbiddenKeywordError, CypherSyntaxError, CypherTimeoutError,
    EngineWriteAttemptedError,
)


@pytest.mark.parametrize("kw", [
    "CREATE", "MERGE", "DELETE", "DETACH DELETE", "SET", "REMOVE",
    "DROP", "LOAD CSV", "USING PERIODIC COMMIT", "CALL",
])
def test_forbidden_keyword_detected(kw):
    query = f"MATCH (n) {kw} n.x = 1 RETURN n"
    assert forbidden_keyword(query) is not None


def test_forbidden_keyword_case_insensitive():
    assert forbidden_keyword("match (n) delete n") is not None
    assert forbidden_keyword("match (n) Delete n") is not None
    assert forbidden_keyword("match (n) DELETE n") is not None


def test_forbidden_keyword_word_boundary():
    assert forbidden_keyword("MATCH (n) WHERE n.name = 'CREATEd' RETURN n") is None
    assert forbidden_keyword("CREATE (n:X) RETURN n") is not None


def test_forbidden_keyword_returns_uppercased_name():
    assert forbidden_keyword("match (n) delete n") == "DELETE"


def test_forbidden_keyword_returns_none_for_safe_query():
    assert forbidden_keyword("MATCH (n) RETURN n LIMIT 10") is None


def test_forbidden_keyword_detects_detach_delete_as_whole():
    assert forbidden_keyword("MATCH (n) DETACH DELETE n") in ("DETACH DELETE", "DELETE")


def test_forbidden_keyword_detects_load_csv():
    assert forbidden_keyword("LOAD CSV FROM 'x' AS row RETURN row") in ("LOAD CSV", "LOAD")


def test_auto_limit_appends_when_missing():
    assert auto_limit("MATCH (n) RETURN n", 100) == "MATCH (n) RETURN n LIMIT 100"


def test_auto_limit_preserves_existing_limit():
    q = "MATCH (n) RETURN n LIMIT 5"
    assert auto_limit(q, 100) == q


def test_auto_limit_preserves_existing_limit_case_insensitive():
    q = "MATCH (n) RETURN n limit 5"
    assert auto_limit(q, 100) == q


def test_auto_limit_strips_trailing_semicolon():
    assert auto_limit("MATCH (n) RETURN n;", 50) == "MATCH (n) RETURN n LIMIT 50"


def test_auto_limit_strips_trailing_whitespace():
    assert auto_limit("MATCH (n) RETURN n   \n  ", 50) == "MATCH (n) RETURN n LIMIT 50"


def test_inject_project_when_params_none():
    assert inject_project(None, "sample") == {"project": "sample"}


def test_inject_project_when_omitted():
    assert inject_project({"q": "foo"}, "sample") == {"q": "foo", "project": "sample"}


def test_inject_project_preserves_caller_override():
    result = inject_project({"project": "other"}, "sample")
    assert result["project"] == "other"


def test_forbidden_keyword_error_carries_query():
    err = ForbiddenKeywordError("DELETE", "MATCH (n) DELETE n")
    assert err.keyword == "DELETE"
    assert err.query == "MATCH (n) DELETE n"
    assert err.code == "forbidden_keyword"
    assert "DELETE" in str(err)


def test_cypher_syntax_error_carries_message():
    err = CypherSyntaxError("unexpected token", "MATCH x")
    assert err.code == "cypher_syntax"
    assert err.query == "MATCH x"


def test_cypher_timeout_error_carries_seconds():
    err = CypherTimeoutError(30, "MATCH (n) RETURN n")
    assert err.code == "timeout"
    assert "30" in str(err)


def test_engine_write_attempted_error_carries_query():
    err = EngineWriteAttemptedError("CREATE (n) RETURN n")
    assert err.code == "engine_write_attempted"
    assert err.query == "CREATE (n) RETURN n"
