from __future__ import annotations

import hashlib
from typing import Any

from livegraph.check.staleness import probe_staleness


class _FakeBackend:
    def __init__(self, stored: dict[str, str]):
        self._stored = stored
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        return [{"path": p, "hash": h} for p, h in self._stored.items()]

    def verify(self): return None
    def close(self): return None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_no_drift_when_hashes_match(tmp_path):
    (tmp_path / "a.py").write_text("def f(): pass\n")
    backend = _FakeBackend({"a.py": _sha("def f(): pass\n")})
    report = probe_staleness(str(tmp_path), backend, project="p")
    assert report.drifted_files == 0
    assert report.has_drift is False


def test_drift_counted_when_disk_differs(tmp_path):
    (tmp_path / "a.py").write_text("def f(): pass\n")
    backend = _FakeBackend({"a.py": "deadbeef"})
    report = probe_staleness(str(tmp_path), backend, project="p")
    assert report.drifted_files == 1
    assert report.has_drift is True


def test_drift_counts_file_only_on_disk(tmp_path):
    (tmp_path / "new.py").write_text("def g(): pass\n")
    backend = _FakeBackend({})
    report = probe_staleness(str(tmp_path), backend, project="p")
    assert report.drifted_files == 1


def test_drift_counts_file_only_in_graph(tmp_path):
    backend = _FakeBackend({"gone.py": _sha("anything")})
    report = probe_staleness(str(tmp_path), backend, project="p")
    assert report.drifted_files == 1


def test_multiple_files_counted(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    backend = _FakeBackend({
        "a.py": "wronghash",
        "b.py": _sha("y = 2\n"),
    })
    report = probe_staleness(str(tmp_path), backend, project="p")
    assert report.drifted_files == 1
