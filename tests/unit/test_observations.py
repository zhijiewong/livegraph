import types

from livegraph.runtime.observations import qid_from_code


def _code_of(func) -> types.CodeType:
    return func.__code__


def test_qid_for_module_function(tmp_path):
    src = "def target():\n    return 1\n"
    path = tmp_path / "m.py"
    path.write_text(src)
    namespace: dict = {}
    exec(compile(src, str(path), "exec"), namespace)  # noqa: S102
    qid = qid_from_code(_code_of(namespace["target"]), str(tmp_path))
    assert qid == "m.py::target"


def test_qid_for_method(tmp_path):
    src = "class C:\n    def run(self):\n        return 2\n"
    path = tmp_path / "m.py"
    path.write_text(src)
    namespace: dict = {}
    exec(compile(src, str(path), "exec"), namespace)  # noqa: S102
    qid = qid_from_code(_code_of(namespace["C"].run), str(tmp_path))
    assert qid == "m.py::C.run"


def test_qid_outside_root_is_none(tmp_path):
    src = "def f():\n    return 1\n"
    namespace: dict = {}
    exec(compile(src, "/elsewhere/x.py", "exec"), namespace)  # noqa: S102
    assert qid_from_code(_code_of(namespace["f"]), str(tmp_path)) is None
