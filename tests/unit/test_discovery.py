from livegraph.discovery import discover_python_files


def test_discovers_py_files_relative(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1")
    (tmp_path / "b.py").write_text("y = 2")
    found = set(discover_python_files(str(tmp_path)))
    assert found == {"b.py", "pkg/a.py"}


def test_skips_junk_directories(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("z = 3")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "c.py").write_text("c = 4")
    (tmp_path / "real.py").write_text("r = 5")
    assert set(discover_python_files(str(tmp_path))) == {"real.py"}
