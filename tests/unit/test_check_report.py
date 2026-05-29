from __future__ import annotations

import json

from livegraph.check.models import CheckResult, Report, StalenessReport
from livegraph.check.report import render_json, render_text


def _r(
    project="myproj", drifted=0, results=(), warnings=(),
) -> Report:
    return Report(
        project=project,
        staleness=StalenessReport(drifted_files=drifted),
        results=tuple(results),
        warnings=tuple(warnings),
    )


def test_text_renders_pass_and_fail():
    report = _r(results=(
        CheckResult(check="cycles", status="passed",
                    actual=0, threshold=0),
        CheckResult(check="layering", status="failed",
                    actual=4, threshold=0,
                    items=tuple({"from_file": f"a{i}.py",
                                 "to_file": f"b{i}.py",
                                 "edge_kind": "imports"}
                                 for i in range(4))),
    ))
    text = render_text(report)
    assert "[PASS] cycles" in text
    assert "[FAIL] layering" in text
    assert "4 / max 0" in text
    assert "a0.py" in text
    assert "Summary:" in text


def test_text_truncates_items_with_count():
    items = tuple({"from_file": f"a{i}.py", "to_file": "b.py",
                   "edge_kind": "imports"} for i in range(10))
    report = _r(results=(
        CheckResult(check="layering", status="failed",
                    actual=10, threshold=0, items=items),
    ))
    text = render_text(report)
    assert "... 5 more" in text


def test_text_shows_staleness_warning():
    report = _r(drifted=3)
    text = render_text(report)
    assert "staleness" in text.lower()
    assert "3" in text


def test_text_renders_skipped_check():
    report = _r(results=(
        CheckResult(check="hubs", status="skipped",
                    reason="disabled in config"),
    ))
    text = render_text(report)
    assert "[SKIP] hubs" in text


def test_json_renders_all_fields():
    report = _r(
        drifted=2,
        results=(
            CheckResult(check="cycles", status="passed",
                        actual=0, threshold=0),
        ),
        warnings=("unknown_thing",),
    )
    parsed = json.loads(render_json(report, exit_code=0))
    assert parsed["project"] == "myproj"
    assert parsed["graph_staleness"]["drifted_files"] == 2
    assert parsed["results"][0]["check"] == "cycles"
    assert parsed["results"][0]["status"] == "passed"
    assert parsed["summary"]["passed"] == 1
    assert parsed["exit_code"] == 0
    assert parsed["warnings"] == ["unknown_thing"]


def test_json_includes_full_items_no_truncation():
    items = tuple({"id": i} for i in range(50))
    report = _r(results=(
        CheckResult(check="x", status="failed",
                    actual=50, threshold=0, items=items),
    ))
    parsed = json.loads(render_json(report, exit_code=1))
    assert len(parsed["results"][0]["items"]) == 50
