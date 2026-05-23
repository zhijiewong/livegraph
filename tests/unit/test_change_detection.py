import hashlib

from livegraph.graph.backend import FakeBackend
from livegraph.incremental import detect_changes, ChangeSet


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_detect_changes_all_unchanged(tmp_path):
    src = "x = 1\n"
    (tmp_path / "a.py").write_text(src)
    h = _hash(src)
    backend = FakeBackend(rows=[{"path": "a.py", "hash": h}])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.unchanged == ["a.py"]
    assert cs.changed == []
    assert cs.added == []
    assert cs.deleted == []


def test_detect_changes_one_changed(tmp_path):
    (tmp_path / "a.py").write_text("x = 2\n")
    backend = FakeBackend(rows=[{"path": "a.py", "hash": _hash("x = 1\n")}])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.changed == ["a.py"]
    assert cs.unchanged == []


def test_detect_changes_added(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    backend = FakeBackend(rows=[])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.added == ["a.py"]
    assert cs.changed == []
    assert cs.deleted == []


def test_detect_changes_deleted(tmp_path):
    backend = FakeBackend(rows=[{"path": "gone.py", "hash": "abc"}])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.deleted == ["gone.py"]
    assert cs.added == []


def test_detect_changes_null_stored_hash_is_changed(tmp_path):
    """Pre-Phase-5 graphs have content_hash=None; treat as changed."""
    (tmp_path / "a.py").write_text("x = 1\n")
    backend = FakeBackend(rows=[{"path": "a.py", "hash": None}])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.changed == ["a.py"]


def test_detect_changes_mixed(tmp_path):
    (tmp_path / "stay.py").write_text("a\n")
    (tmp_path / "edit.py").write_text("new content\n")
    (tmp_path / "new.py").write_text("freshly added\n")
    backend = FakeBackend(rows=[
        {"path": "stay.py", "hash": _hash("a\n")},
        {"path": "edit.py", "hash": _hash("old content\n")},
        {"path": "gone.py", "hash": "anything"},
    ])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.unchanged == ["stay.py"]
    assert cs.changed == ["edit.py"]
    assert cs.added == ["new.py"]
    assert cs.deleted == ["gone.py"]
    _q, params = backend.calls[0]
    assert params["project"] == "p"


def test_change_set_carries_fresh_hashes(tmp_path):
    src = "x = 1\n"
    (tmp_path / "a.py").write_text(src)
    backend = FakeBackend(rows=[])
    cs = detect_changes(str(tmp_path), backend, project="p")
    assert cs.hashes == {"a.py": _hash(src)}
