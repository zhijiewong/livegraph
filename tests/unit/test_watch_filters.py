from __future__ import annotations

from pathlib import Path

from livegraph.watch.filters import PathFilter


def make_filter(tmp_path: Path, **kw) -> PathFilter:
    return PathFilter(root=tmp_path, **kw)


def test_non_python_files_excluded(tmp_path):
    pf = make_filter(tmp_path)
    assert pf.accepts(tmp_path / "a.txt") is False
    assert pf.accepts(tmp_path / "a.py") is True


def test_builtin_ignores(tmp_path):
    pf = make_filter(tmp_path)
    assert pf.accepts(tmp_path / ".git" / "HEAD.py") is False
    assert pf.accepts(tmp_path / "__pycache__" / "x.py") is False
    assert pf.accepts(tmp_path / ".venv" / "lib" / "a.py") is False
    assert pf.accepts(tmp_path / "venv" / "lib" / "a.py") is False
    assert pf.accepts(tmp_path / "node_modules" / "a.py") is False


def test_user_ignore_globs(tmp_path):
    pf = make_filter(tmp_path, user_ignores=["build/*", "*_pb2.py"])
    assert pf.accepts(tmp_path / "build" / "x.py") is False
    assert pf.accepts(tmp_path / "pkg" / "foo_pb2.py") is False
    assert pf.accepts(tmp_path / "pkg" / "foo.py") is True


def test_gitignore_respected(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored/\n*.gen.py\n")
    pf = make_filter(tmp_path)
    assert pf.accepts(tmp_path / "ignored" / "x.py") is False
    assert pf.accepts(tmp_path / "a.gen.py") is False
    assert pf.accepts(tmp_path / "a.py") is True


def test_paths_outside_root_rejected(tmp_path):
    pf = make_filter(tmp_path)
    assert pf.accepts(Path("/etc/passwd.py")) is False


def test_typescript_files_accepted(tmp_path):
    pf = make_filter(tmp_path)
    for name in ["a.ts", "b.tsx", "c.js", "d.jsx", "e.mjs", "f.cjs"]:
        assert pf.accepts(tmp_path / name) is True


def test_other_extensions_rejected(tmp_path):
    pf = make_filter(tmp_path)
    assert pf.accepts(tmp_path / "x.go") is False
    assert pf.accepts(tmp_path / "x.rs") is False
