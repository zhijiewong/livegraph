# tests/unit/test_models.py
from livegraph.models import Definition, FileRecord, ImportRecord, CallEdge


def test_file_record_defaults():
    f = FileRecord(path="a/b.py", name="b.py")
    assert f.language == "python"
    assert f.parse_error is False


def test_definition_is_frozen():
    d = Definition(qualified_name="a.py::f", name="f", kind="function",
                   file="a.py", start_line=1, end_line=2,
                   decorators=(), source="def f(): pass")
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.name = "g"  # type: ignore[misc]


def test_call_edge_defaults():
    c = CallEdge(caller_qn="a.py::f", callee_qn="a.py::g")
    assert c.static is False and c.runtime is False
    assert c.observed_count == 0 and c.call_site_lines == ()


def test_import_record():
    i = ImportRecord(file="a.py", raw="import os", line=1, module="os")
    assert i.module == "os"
