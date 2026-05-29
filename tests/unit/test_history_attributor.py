from __future__ import annotations

from typing import Any

from livegraph.history.attributor import attribute_hunks
from livegraph.history.models import HunkRange


class _FakeBackend:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        return self._rows

    def verify(self): return None
    def close(self): return None


def test_no_hunks_returns_empty_dict():
    backend = _FakeBackend([])
    out = attribute_hunks(backend, project="p", file_path="a.py", hunks=())
    assert out == {}


def test_no_overlapping_symbols_returns_empty_dict():
    backend = _FakeBackend([
        {"qualified_name": "pkg.foo", "start_line": 1, "end_line": 5},
    ])
    out = attribute_hunks(
        backend, project="p", file_path="a.py",
        hunks=(HunkRange(start=20, end=22),),
    )
    assert out == {}


def test_single_overlap_returns_lines_count():
    backend = _FakeBackend([
        {"qualified_name": "pkg.foo", "start_line": 1, "end_line": 10},
    ])
    out = attribute_hunks(
        backend, project="p", file_path="a.py",
        hunks=(HunkRange(start=5, end=7),),
    )
    assert out == {"pkg.foo": 3}


def test_multiple_hunks_accumulate_per_symbol():
    backend = _FakeBackend([
        {"qualified_name": "pkg.foo", "start_line": 1, "end_line": 10},
        {"qualified_name": "pkg.bar", "start_line": 11, "end_line": 20},
    ])
    out = attribute_hunks(
        backend, project="p", file_path="a.py",
        hunks=(
            HunkRange(start=2, end=4),    # foo: 3 lines
            HunkRange(start=8, end=12),   # foo: 8..10 (3) + bar: 11..12 (2)
            HunkRange(start=18, end=18),  # bar: 1 line
        ),
    )
    assert out == {"pkg.foo": 6, "pkg.bar": 3}


def test_query_scopes_to_project_and_file():
    backend = _FakeBackend([])
    attribute_hunks(
        backend, project="myproj", file_path="pkg/a.py",
        hunks=(HunkRange(start=1, end=1),),
    )
    assert backend.calls, "expected at least one backend.execute call"
    cypher, params = backend.calls[0]
    assert params["project"] == "myproj"
    assert params["file"] == "pkg/a.py"
