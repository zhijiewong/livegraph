from __future__ import annotations

from livegraph.models import ImportRecord
from livegraph.static.extractor import RawCall
from livegraph.static_ts.resolver import resolve_calls, resolve_imports
from livegraph.static_ts.tsconfig import TsConfig


def test_relative_import_resolves_with_extension_inference():
    imports = [ImportRecord(
        file="src/index.ts", module="./calc", raw="./calc", line=1,
    )]
    project_files = {"src/calc.ts", "src/index.ts"}
    resolved = resolve_imports(
        imports, project_files=project_files, tsconfig=TsConfig(),
    )
    assert len(resolved) == 1
    r = resolved[0]
    assert r.file == "src/index.ts"
    assert r.target == "src/calc.ts"
    assert r.target_kind == "file"


def test_relative_import_resolves_to_index_when_directory():
    imports = [ImportRecord(
        file="src/index.ts", module="./store", raw="./store", line=1,
    )]
    project_files = {"src/store/index.ts", "src/index.ts"}
    resolved = resolve_imports(
        imports, project_files=project_files, tsconfig=TsConfig(),
    )
    assert resolved[0].target == "src/store/index.ts"


def test_tsconfig_alias_resolves():
    imports = [ImportRecord(
        file="src/index.ts", module="@/util", raw="@/util", line=1,
    )]
    project_files = {"src/util.ts", "src/index.ts"}
    cfg = TsConfig(base_url="./src", paths={"@/util": ["util.ts"]})
    resolved = resolve_imports(
        imports, project_files=project_files, tsconfig=cfg,
    )
    assert resolved[0].target == "src/util.ts"
    assert resolved[0].target_kind == "file"


def test_bare_module_recorded_as_thirdparty():
    imports = [ImportRecord(
        file="src/index.ts", module="react", raw="react", line=1,
    )]
    resolved = resolve_imports(
        imports, project_files=set(), tsconfig=TsConfig(),
    )
    assert resolved[0].target == "react"
    assert resolved[0].target_kind == "thirdparty"


def test_resolve_calls_matches_defined_symbols():
    raw = [
        RawCall(caller_qn="src/a.ts::f", callee_name="bar", line=1),
        RawCall(caller_qn="src/a.ts::f", callee_name="missing", line=2),
    ]
    defined = {"src/a.ts::f", "src/b.ts::bar"}
    edges = resolve_calls(raw, defined)
    callees = {e.callee_qn for e in edges}
    assert "src/b.ts::bar" in callees
    # 'missing' has no defined symbol — no edge.
