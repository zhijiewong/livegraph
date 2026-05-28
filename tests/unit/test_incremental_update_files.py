from __future__ import annotations


from livegraph.incremental import UpdateSummary, update_files


class _QueuedBackend:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
    def verify(self): return None
    def execute(self, cypher, **params):
        self.calls.append((cypher, params))
        if not self._responses:
            return []
        return self._responses.pop(0)
    def close(self): return None


def test_empty_paths_is_noop(tmp_path):
    backend = _QueuedBackend([])
    summary = update_files(str(tmp_path), backend, project="p", paths=[])
    assert summary == UpdateSummary(0, 0, 0, 0, 0)
    assert backend.calls == []  # no stored-hash query either


def test_paths_outside_root_ignored(tmp_path):
    backend = _QueuedBackend([])
    summary = update_files(
        str(tmp_path), backend, project="p",
        paths=["/etc/passwd.py", "/var/log/x.py"],
    )
    assert summary == UpdateSummary(0, 0, 0, 0, 0)
    assert backend.calls == []


def test_path_not_in_graph_and_exists_classified_added(tmp_path):
    # New file, not yet in graph
    target = tmp_path / "pkg" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("def f():\n    return 1\n")

    # First execute: stored-hash query returns nothing (file not in graph)
    # Subsequent executes: whatever reingest_files needs. We just need enough
    # canned responses to let it finish; reingest_files makes several writes.
    # Provide a large pool of empty responses.
    backend = _QueuedBackend([[]] * 50)

    summary = update_files(str(tmp_path), backend, project="p",
                           paths=[str(target)])
    assert summary.added == 1
    assert summary.changed == 0
    assert summary.deleted == 0


def test_path_in_graph_with_different_hash_classified_changed(tmp_path):
    target = tmp_path / "m.py"
    target.write_text("def f():\n    return 1\n")

    # Stored-hash query returns a different hash
    backend = _QueuedBackend([
        [{"path": "m.py", "hash": "OLD_HASH_DIFFERENT_FROM_DISK"}],
    ] + [[]] * 50)

    summary = update_files(str(tmp_path), backend, project="p",
                           paths=[str(target)])
    assert summary.changed == 1
    assert summary.added == 0
    assert summary.deleted == 0


def test_path_in_graph_with_same_hash_is_noop(tmp_path):
    import hashlib
    target = tmp_path / "m.py"
    src = b"def f():\n    return 1\n"
    target.write_bytes(src)
    h = hashlib.sha256(src).hexdigest()

    backend = _QueuedBackend([
        [{"path": "m.py", "hash": h}],
    ])
    summary = update_files(str(tmp_path), backend, project="p",
                           paths=[str(target)])
    assert summary == UpdateSummary(0, 0, 0, 0, 0)
    # Only the stored-hash query should have run; no reingest writes.
    assert len(backend.calls) == 1


def test_path_missing_on_disk_and_in_graph_classified_deleted(tmp_path):
    backend = _QueuedBackend([
        [{"path": "gone.py", "hash": "any"}],  # stored
    ] + [[]] * 20)
    summary = update_files(str(tmp_path), backend, project="p",
                           paths=[str(tmp_path / "gone.py")])
    assert summary.deleted == 1
    assert summary.added == 0
    assert summary.changed == 0


def test_relative_paths_accepted(tmp_path):
    target = tmp_path / "m.py"
    target.write_text("def f():\n    return 1\n")
    backend = _QueuedBackend([[]] + [[]] * 50)
    # Pass a relative path (relative to CWD); update_files should still
    # normalize using `root` not CWD. Use cwd-trick:
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        summary = update_files(str(tmp_path), backend, project="p",
                               paths=["m.py"])
    finally:
        os.chdir(cwd)
    assert summary.added == 1
