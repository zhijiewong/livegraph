from livegraph.models import ImportRecord
from livegraph.static.extractor import RawCall
from livegraph.static.resolver import (
    resolve_imports, resolve_calls, ResolvedImport,
)


def test_resolve_internal_import_to_file():
    project_modules = {"pkg.sub": "pkg/sub.py", "pkg": "pkg/__init__.py"}
    imports = [ImportRecord("a.py", "from pkg.sub import x", 1, "pkg.sub")]
    resolved = resolve_imports(imports, project_modules)
    assert resolved == [
        ResolvedImport(file="a.py", target="pkg/sub.py", target_kind="file",
                       raw="from pkg.sub import x", line=1)
    ]


def test_resolve_stdlib_import():
    resolved = resolve_imports(
        [ImportRecord("a.py", "import os", 1, "os")], {})
    assert resolved[0].target == "os"
    assert resolved[0].target_kind == "stdlib"


def test_resolve_thirdparty_import():
    resolved = resolve_imports(
        [ImportRecord("a.py", "import numpy", 1, "numpy")], {})
    assert resolved[0].target == "numpy"
    assert resolved[0].target_kind == "thirdparty"


def test_resolve_calls_prefers_same_file():
    defined = {"a.py::f", "b.py::f"}
    calls = [RawCall(caller_qn="a.py::g", callee_name="f", line=2)]
    edges = resolve_calls(calls, defined)
    assert len(edges) == 1
    assert edges[0].caller_qn == "a.py::g"
    assert edges[0].callee_qn == "a.py::f"
    assert edges[0].static is True


def test_resolve_calls_drops_unresolved():
    edges = resolve_calls(
        [RawCall("a.py::g", "print", 1)], defined={"a.py::g"})
    assert edges == []
