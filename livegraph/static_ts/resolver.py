"""Resolve TS imports and calls against the project's defined symbols."""
from __future__ import annotations

import os
from collections.abc import Iterable

from livegraph.models import CallEdge, ImportRecord
from livegraph.static.extractor import RawCall
from livegraph.static.resolver import ResolvedImport
from livegraph.static_ts.tsconfig import TsConfig

_TS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def resolve_imports(
    imports: Iterable[ImportRecord],
    *,
    project_files: set[str],
    tsconfig: TsConfig,
) -> list[ResolvedImport]:
    """Resolve TS import specifiers to project files or external modules."""
    out: list[ResolvedImport] = []
    for imp in imports:
        spec = imp.module
        resolved = _resolve_specifier(
            spec, importing_file=imp.file,
            project_files=project_files, tsconfig=tsconfig,
        )
        if resolved is not None:
            out.append(ResolvedImport(
                file=imp.file, target=resolved, target_kind="file",
                raw=imp.raw, line=imp.line,
            ))
        else:
            # Bare specifier or alias that doesn't match a project file:
            # record as thirdparty (matches Python pipeline convention).
            out.append(ResolvedImport(
                file=imp.file, target=spec, target_kind="thirdparty",
                raw=imp.raw, line=imp.line,
            ))
    return out


def _resolve_specifier(
    spec: str, *, importing_file: str,
    project_files: set[str], tsconfig: TsConfig,
) -> str | None:
    """Return the project-relative file path the specifier resolves to."""
    if spec.startswith("./") or spec.startswith("../"):
        importer_dir = os.path.dirname(importing_file)
        candidate = os.path.normpath(os.path.join(importer_dir, spec))
        candidate = candidate.replace("\\", "/")
        return _expand_to_existing_file(candidate, project_files)
    aliased = tsconfig.resolve_alias(spec)
    if aliased is not None:
        return _expand_to_existing_file(aliased, project_files)
    return None


def _expand_to_existing_file(
    candidate: str, project_files: set[str],
) -> str | None:
    """Try the literal path, then ext suffixes, then index files."""
    if candidate in project_files:
        return candidate
    for ext in _TS_EXTS:
        if (candidate + ext) in project_files:
            return candidate + ext
    for ext in _TS_EXTS:
        idx = f"{candidate}/index{ext}"
        if idx in project_files:
            return idx
    return None


def resolve_calls(
    raw_calls: Iterable[RawCall], defined: set[str],
) -> list[CallEdge]:
    """Match raw call sites by simple-name against the project's symbols.

    Mirrors the Python resolver: a callee_name matches a defined qname
    whose last dotted segment equals it. Multiple matches produce one
    edge (preferring same-file matches), to match the Python resolver
    behavior. Sets ``static=True``; TS has no runtime tracing in this phase.
    """
    by_simple: dict[str, list[str]] = {}
    for qn in defined:
        # qn shape: "rel/path::dotted.name" — split off file prefix first.
        dotted = qn.split("::", 1)[1] if "::" in qn else qn
        simple = dotted.split(".")[-1]
        by_simple.setdefault(simple, []).append(qn)

    edges: dict[tuple[str, str], CallEdge] = {}
    for call in raw_calls:
        simple = call.callee_name.split(".")[-1]
        candidates = by_simple.get(simple)
        if not candidates:
            continue
        caller_file = call.caller_qn.split("::", 1)[0]
        same_file = [c for c in candidates
                     if c.split("::", 1)[0] == caller_file]
        callee_qn = same_file[0] if same_file else candidates[0]
        key = (call.caller_qn, callee_qn)
        if key not in edges:
            edges[key] = CallEdge(
                caller_qn=call.caller_qn, callee_qn=callee_qn,
                static=True, runtime=False,
            )
    return list(edges.values())
