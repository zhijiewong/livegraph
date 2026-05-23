from typing import Any

from livegraph.incremental import (
    ChangeSet, UpdateSummary, reingest_files,
)


class _QueuedBackend:
    """Test backend returning a different canned response per execute call."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def verify(self) -> None:
        return None

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        if not self._responses:
            return []
        return self._responses.pop(0)

    def close(self) -> None:
        return None


def test_reingest_empty_changeset_returns_zero_summary(tmp_path):
    backend = _QueuedBackend([])
    cs = ChangeSet(added=[], changed=[], deleted=[], unchanged=[], hashes={})
    summary = reingest_files(str(tmp_path), backend, project="p",
                             changeset=cs, batch_size=100)
    assert summary == UpdateSummary(
        added=0, changed=0, deleted=0, unchanged=0, parse_errors=0,
    )
    assert backend.calls == []


def test_reingest_deletes_for_each_deleted_path(tmp_path):
    backend = _QueuedBackend([[], []])
    cs = ChangeSet(added=[], changed=[],
                   deleted=["a.py", "b.py"], unchanged=[], hashes={})
    summary = reingest_files(str(tmp_path), backend, project="p",
                             changeset=cs, batch_size=100)
    assert summary.deleted == 2
    delete_calls = [c for c in backend.calls if "DETACH DELETE" in c[0]]
    files_deleted = {c[1]["file"] for c in delete_calls if "file" in c[1]}
    assert files_deleted == {"a.py", "b.py"}


def test_reingest_changed_file_does_full_reconcile(tmp_path):
    src = "def f():\n    return 1\n"
    (tmp_path / "m.py").write_text(src)

    backend = _QueuedBackend([
        [{"qn": "m.py::old"}],                         # old qns for m.py
        [],                                            # delete_symbols
        [],                                            # write_files
        [],                                            # write_definitions
        [],                                            # delete_outgoing_calls
        [],                                            # delete_imports
        [],                                            # flag_runtime_stale
        [{"qualified_name": "m.py::f"}],               # project_defined
        [],                                            # project_modules
    ])
    cs = ChangeSet(
        added=[], changed=["m.py"], deleted=[], unchanged=[],
        hashes={"m.py": "newhash"},
    )
    summary = reingest_files(str(tmp_path), backend, project="p",
                             changeset=cs, batch_size=100)

    assert summary.changed == 1
    assert any(
        "DETACH DELETE" in q and "m.py::old" in p.get("qns", [])
        for q, p in backend.calls
    )
    merge_files = [c for c in backend.calls if "MERGE (p:Project" in c[0]]
    assert merge_files
    assert merge_files[0][1]["rows"][0]["content_hash"] == "newhash"
    assert any("[c:CALLS]" in q and "DELETE c" in q for q, _ in backend.calls)
    assert any("[r:IMPORTS]" in q and "DELETE r" in q for q, _ in backend.calls)
    assert any("SET s.runtime_stale = true" in q for q, _ in backend.calls)


def test_reingest_added_file_skips_old_qn_query(tmp_path):
    """For added files, there are no old qns to query/delete."""
    (tmp_path / "new.py").write_text("def f():\n    return 1\n")
    backend = _QueuedBackend([
        [],                                            # write_files
        [],                                            # write_definitions
        [],                                            # delete_outgoing_calls
        [],                                            # delete_imports
        [],                                            # flag_runtime_stale
        [{"qualified_name": "new.py::f"}],             # project_defined
        [],                                            # project_modules
    ])
    cs = ChangeSet(
        added=["new.py"], changed=[], deleted=[], unchanged=[],
        hashes={"new.py": "h1"},
    )
    summary = reingest_files(str(tmp_path), backend, project="p",
                             changeset=cs, batch_size=100)
    assert summary.added == 1
    detach_symbol_calls = [
        c for c in backend.calls
        if "DETACH DELETE s" in c[0] and "OPTIONAL MATCH" not in c[0]
    ]
    assert detach_symbol_calls == []


def test_reingest_parse_error_records_file_but_skips_symbol_writes(tmp_path):
    (tmp_path / "bad.py").write_text("def f(:\n")
    backend = _QueuedBackend([
        [],                                            # old qns (returns empty)
        [],                                            # write_files (parse_error=true)
        [],                                            # project_defined
        [],                                            # project_modules
    ])
    cs = ChangeSet(
        added=[], changed=["bad.py"], deleted=[], unchanged=[],
        hashes={"bad.py": "h"},
    )
    summary = reingest_files(str(tmp_path), backend, project="p",
                             changeset=cs, batch_size=100)
    assert summary.parse_errors == 1
    assert summary.changed == 1
