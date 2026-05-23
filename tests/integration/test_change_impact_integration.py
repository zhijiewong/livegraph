"""End-to-end Cypher tests for the change_impact MCP tool."""
import pytest

from livegraph.mcp import tools

pytestmark = pytest.mark.integration


# A diff that touches a line inside `Calculator.add`. The hunk header
# uses start line 7 (the `def add(...)` header) and the touched `+` lines
# land at the body of `add`, which should overlap the method's
# [start_line, end_line] span recorded by Phase 1.
_DIFF_TOUCHING_ADD = (
    "diff --git a/calculator.py b/calculator.py\n"
    "--- a/calculator.py\n"
    "+++ b/calculator.py\n"
    "@@ -7,3 +7,3 @@ class Calculator:\n"
    "     def add(self, a, b):\n"
    "-        return a + b\n"
    "+        return a + b + 0\n"
)


def test_change_impact_finds_runtime_only_dynamic_dispatch_caller(
    ingested_sample,
):
    """Phase 4 acceptance test.

    A diff that changes Calculator.add must impact runner.py::run_operation
    via a runtime-observed edge — the dynamic-dispatch caller no purely
    static blast-radius tool can find.
    """
    backend, project = ingested_sample
    result = tools.change_impact(backend, project, diff=_DIFF_TOUCHING_ADD)

    changed_qns = {c["qualified_name"] for c in result["changed"]}
    assert "calculator.py::Calculator.add" in changed_qns

    impacted_qns = {i["qualified_name"] for i in result["impacted"]}
    assert "runner.py::run_operation" in impacted_qns

    run_op = next(
        i for i in result["impacted"]
        if i["qualified_name"] == "runner.py::run_operation"
    )
    assert any(
        any(edge["runtime"] for edge in entry["edges"])
        for entry in run_op["reached_via"]
    )
    assert min(entry["depth"] for entry in run_op["reached_via"]) == 1


def test_change_impact_returns_tests_to_run(ingested_sample):
    backend, project = ingested_sample
    result = tools.change_impact(backend, project, diff=_DIFF_TOUCHING_ADD)
    test_qns = {t["qualified_name"] for t in result["tests_to_run"]}
    assert test_qns, f"expected at least one test, got {result['tests_to_run']!r}"


def test_change_impact_unmatched_files_for_unknown_path(ingested_sample):
    backend, project = ingested_sample
    diff = (
        "diff --git a/never_ingested.py b/never_ingested.py\n"
        "--- a/never_ingested.py\n"
        "+++ b/never_ingested.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    result = tools.change_impact(backend, project, diff=diff)
    assert result["unmatched_files"] == ["never_ingested.py"]
    assert result["changed"] == []
    assert result["impacted"] == []
    assert result["tests_to_run"] == []
    assert result["stats"]["changed_files"] == 1
    assert result["stats"]["changed_symbols"] == 0
