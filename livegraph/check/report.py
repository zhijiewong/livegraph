"""Render a Report to text or JSON for the `livegraph check` CLI."""
from __future__ import annotations

import json
from typing import Any

from livegraph.check.models import Report

_TEXT_ITEM_CAP = 5


def render_text(report: Report) -> str:
    lines: list[str] = []
    lines.append(f"Project: {report.project}")
    if report.staleness.has_drift:
        lines.append(
            f"Graph staleness: {report.staleness.drifted_files} "
            f"files drifted from disk (run `livegraph update`)"
        )
    lines.append("")
    for r in report.results:
        if r.status == "passed":
            lines.append(
                f"[PASS] {r.check:<24} {r.actual} / max {r.threshold}"
            )
        elif r.status == "failed":
            lines.append(
                f"[FAIL] {r.check:<24} {r.actual} / max {r.threshold}"
            )
        elif r.status == "skipped":
            lines.append(
                f"[SKIP] {r.check:<24} {r.reason or ''}"
            )
        else:  # error
            lines.append(
                f"[ERR ] {r.check:<24} {r.reason or ''}"
            )
        if r.items:
            shown = r.items[:_TEXT_ITEM_CAP]
            for item in shown:
                lines.append(f"  {_format_item(item)}")
            extra = len(r.items) - len(shown)
            if extra > 0:
                lines.append(f"  ... {extra} more")
    lines.append("")
    warns = (
        f", warnings: {len(report.warnings) + (1 if report.staleness.has_drift else 0)}"
        if report.warnings or report.staleness.has_drift
        else ""
    )
    lines.append(
        f"Summary: {report.passed} passed, {report.failed} failed, "
        f"{report.skipped} skipped{warns}"
    )
    return "\n".join(lines)


def _format_item(item: dict[str, Any]) -> str:
    if "from_file" in item and "to_file" in item:
        kind = item.get("edge_kind", "")
        return f"{item['from_file']} -> {item['to_file']} ({kind})"
    if "qualified_name" in item:
        extra = []
        if "in_callers" in item:
            extra.append(f"in={item['in_callers']}")
        if "commit_count" in item:
            extra.append(f"commits={item['commit_count']}")
        suffix = f" [{', '.join(extra)}]" if extra else ""
        return f"{item['qualified_name']}{suffix}"
    if "nodes" in item:
        nodes = item["nodes"]
        head = ", ".join(nodes[:3])
        tail = "" if len(nodes) <= 3 else f" + {len(nodes) - 3} more"
        return f"size={item.get('size', len(nodes))}: {head}{tail}"
    return str(item)


def render_json(report: Report, exit_code: int) -> str:
    payload = {
        "project": report.project,
        "graph_staleness": {
            "drifted_files": report.staleness.drifted_files,
        },
        "results": [
            _result_dict(r) for r in report.results
        ],
        "summary": {
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
        },
        "warnings": list(report.warnings),
        "exit_code": exit_code,
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def _result_dict(r) -> dict[str, Any]:
    out: dict[str, Any] = {"check": r.check, "status": r.status}
    if r.status in ("passed", "failed"):
        out["actual"] = r.actual
        out["threshold"] = r.threshold
    if r.reason is not None:
        out["reason"] = r.reason
    out["items"] = [dict(i) for i in r.items]
    return out
