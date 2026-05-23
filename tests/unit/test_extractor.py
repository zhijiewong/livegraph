from livegraph.static.extractor import extract

SRC = b'''\
import os
from pkg.sub import helper


def top_level(x):
    helper(x)
    return x


class Handler:
    @property
    def name(self):
        return "h"

    def run(self):
        return top_level(1)
'''


def test_extracts_module_function():
    defs, _imports, _calls = extract("a.py", SRC)
    fn = [d for d in defs if d.kind == "function"]
    assert [d.qualified_name for d in fn] == ["a.py::top_level"]
    assert fn[0].start_line == 5


def test_extracts_class_and_methods():
    defs, _imports, _calls = extract("a.py", SRC)
    classes = [d for d in defs if d.kind == "class"]
    methods = [d for d in defs if d.kind == "method"]
    assert [d.qualified_name for d in classes] == ["a.py::Handler"]
    assert {d.qualified_name for d in methods} == {
        "a.py::Handler.name", "a.py::Handler.run",
    }
    assert all(m.parent_class == "a.py::Handler" for m in methods)


def test_extracts_decorators():
    defs, _imports, _calls = extract("a.py", SRC)
    name = next(d for d in defs if d.qualified_name == "a.py::Handler.name")
    assert name.decorators == ("property",)


def test_extracts_imports():
    _defs, imports, _calls = extract("a.py", SRC)
    assert {i.module for i in imports} == {"os", "pkg.sub"}


def test_extracts_raw_calls_with_caller_scope():
    _defs, _imports, calls = extract("a.py", SRC)
    pairs = {(c.caller_qn, c.callee_name) for c in calls}
    assert ("a.py::top_level", "helper") in pairs
    assert ("a.py::Handler.run", "top_level") in pairs


def test_broken_file_yields_empty_results():
    defs, imports, calls = extract("bad.py", b"def f(:\n")
    assert defs == [] and imports == [] and calls == []
