"""Resolve raw imports and calls into graph edges (best-effort, static)."""
from __future__ import annotations

import sys
from dataclasses import dataclass

from livegraph.models import CallEdge, ImportRecord
from livegraph.static.extractor import RawCall


@dataclass(frozen=True, slots=True)
class ResolvedImport:
    """An import resolved to a concrete target."""

    file: str
    target: str          # a File path, or a module name
    target_kind: str     # "file" | "stdlib" | "thirdparty"
    raw: str
    line: int


def resolve_imports(
    imports: list[ImportRecord], project_modules: dict[str, str],
) -> list[ResolvedImport]:
    """Classify each import.

    ``project_modules`` maps a dotted module name to its File path for
    every module inside the project.
    """
    out: list[ResolvedImport] = []
    for imp in imports:
        if imp.module in project_modules:
            out.append(ResolvedImport(imp.file, project_modules[imp.module],
                                      "file", imp.raw, imp.line))
        else:
            top = imp.module.split(".", 1)[0]
            kind = "stdlib" if top in sys.stdlib_module_names else "thirdparty"
            out.append(ResolvedImport(imp.file, imp.module, kind,
                                      imp.raw, imp.line))
    return out


def resolve_calls(
    calls: list[RawCall], defined: set[str],
) -> list[CallEdge]:
    """Resolve raw calls to CALLS edges against project-defined symbols.

    A call's callee name is matched by simple name: first against a
    definition in the caller's own file, then anywhere in the project.
    Unresolved calls (stdlib, third-party, dynamic) are dropped.
    """
    by_simple: dict[str, list[str]] = {}
    for qn in defined:
        simple = qn.split("::", 1)[1].split(".")[-1]
        by_simple.setdefault(simple, []).append(qn)

    edges: dict[tuple[str, str], CallEdge] = {}
    for call in calls:
        simple = call.callee_name.split(".")[-1]
        candidates = by_simple.get(simple)
        if not candidates:
            continue
        caller_file = call.caller_qn.split("::", 1)[0]
        same_file = [c for c in candidates if c.split("::", 1)[0] == caller_file]
        callee_qn = same_file[0] if same_file else candidates[0]
        key = (call.caller_qn, callee_qn)
        if key not in edges:
            edges[key] = CallEdge(caller_qn=call.caller_qn,
                                  callee_qn=callee_qn, static=True)
    return list(edges.values())
