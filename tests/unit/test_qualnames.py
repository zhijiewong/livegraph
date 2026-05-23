from livegraph.qualnames import (
    file_qid, symbol_qid, normalize_co_qualname, rel_path,
)


def test_file_qid_normalizes_separators():
    assert file_qid("src\\app\\h.py") == "src/app/h.py"


def test_symbol_qid_for_function():
    assert symbol_qid("src/app/h.py", "process") == "src/app/h.py::process"


def test_symbol_qid_for_method():
    assert symbol_qid("a.py", "Handler.run") == "a.py::Handler.run"


def test_normalize_strips_locals_segments():
    assert normalize_co_qualname("outer.<locals>.inner") == "outer.inner"
    assert normalize_co_qualname("Handler.run") == "Handler.run"
    assert normalize_co_qualname("plain") == "plain"


def test_rel_path_within_root(tmp_path):
    root = tmp_path
    f = root / "pkg" / "mod.py"
    f.parent.mkdir(parents=True)
    f.touch()
    assert rel_path(str(f), str(root)) == "pkg/mod.py"


def test_rel_path_outside_root_returns_none(tmp_path):
    assert rel_path("/somewhere/else/x.py", str(tmp_path)) is None
