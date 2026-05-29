from __future__ import annotations

from typing import Any

from livegraph.check.checks import (
    check_churn, check_cycles, check_hubs, check_layering,
)
from livegraph.check.config import (
    ChurnConfig, CyclesConfig, HubsConfig, LayeringConfig,
)


class _FakeBackend:
    def __init__(self, responses: dict[str, list[dict[str, Any]]] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        for key, rows in self._responses.items():
            if key in cypher:
                return rows
        return []

    def verify(self): return None
    def close(self): return None


# ---- cycles --------------------------------------------------------

def test_cycles_disabled_is_skipped():
    r = check_cycles(CyclesConfig(enabled=False), _FakeBackend(), "p")
    assert r.status == "skipped"
    assert "disabled" in (r.reason or "")


def test_cycles_passes_when_no_cycles_found():
    # Provide a self-loop so the graph is non-empty, but min_size=2 filters it
    # out — no cycles found, no "no project data" warning.
    backend = _FakeBackend({":IMPORTS": [{"source": "a.py", "target": "a.py"}]})
    r = check_cycles(
        CyclesConfig(enabled=True, scope="module", max_cycles=0),
        backend, "p",
    )
    assert r.status == "passed"
    assert r.actual == 0
    assert r.threshold == 0


def test_cycles_fails_when_cycles_exceed_threshold():
    backend = _FakeBackend({":IMPORTS": [
        {"source": "a.py", "target": "b.py"},
        {"source": "b.py", "target": "a.py"},
    ]})
    r = check_cycles(
        CyclesConfig(enabled=True, scope="module", max_cycles=0),
        backend, "p",
    )
    assert r.status == "failed"
    assert r.actual == 1


# ---- layering ------------------------------------------------------

def test_layering_disabled_is_skipped():
    r = check_layering(LayeringConfig(enabled=False), _FakeBackend(), "p")
    assert r.status == "skipped"


def test_layering_passes_when_no_violations():
    backend = _FakeBackend({
        "(f:File)": [{"path": "web/a.py"}],
        ":IMPORTS": [],
        "CALLS": [],
    })
    r = check_layering(
        LayeringConfig(
            enabled=True,
            layers=({"name": "web", "patterns": ["web/**"]},),
        ),
        backend, "p",
    )
    assert r.status == "passed"


def test_layering_fails_when_violations_exceed_threshold():
    backend = _FakeBackend({
        "(f:File)": [
            {"path": "web/h.py"}, {"path": "domain/c.py"},
        ],
        ":IMPORTS": [
            {"from_file": "domain/c.py", "to_file": "web/h.py"},
        ],
        "CALLS": [],
    })
    r = check_layering(
        LayeringConfig(
            enabled=True, max_violations=0,
            layers=(
                {"name": "web", "patterns": ["web/**"]},
                {"name": "domain", "patterns": ["domain/**"]},
            ),
        ),
        backend, "p",
    )
    assert r.status == "failed"
    assert r.actual >= 1


# ---- churn ---------------------------------------------------------

def test_churn_disabled_is_skipped():
    r = check_churn(ChurnConfig(enabled=False), _FakeBackend(), "p")
    assert r.status == "skipped"


def test_churn_passes_when_no_symbol_exceeds_threshold():
    backend = _FakeBackend({
        "ORDER BY commit_count": [
            {"qualified_name": "pkg.a", "file": "pkg/a.py",
             "kind": "function", "commit_count": 3,
             "unique_authors": 1,
             "first_changed": "2026-05-01", "last_changed": "2026-05-29"},
        ],
    })
    r = check_churn(
        ChurnConfig(enabled=True, window_days=30, hot_files_threshold=10),
        backend, "p",
    )
    assert r.status == "passed"
    assert r.actual == 0


def test_churn_fails_when_hotspot_above_threshold():
    backend = _FakeBackend({
        "ORDER BY commit_count": [
            {"qualified_name": "pkg.hot", "file": "pkg/hot.py",
             "kind": "function", "commit_count": 25,
             "unique_authors": 4,
             "first_changed": "2026-05-01", "last_changed": "2026-05-29"},
        ],
    })
    r = check_churn(
        ChurnConfig(enabled=True, hot_files_threshold=10),
        backend, "p",
    )
    assert r.status == "failed"
    assert r.actual == 1


def test_churn_ignore_glob_excludes_matching_symbols():
    backend = _FakeBackend({
        "ORDER BY commit_count": [
            {"qualified_name": "tests.hot", "file": "tests/hot.py",
             "kind": "function", "commit_count": 99,
             "unique_authors": 4,
             "first_changed": "2026-05-01", "last_changed": "2026-05-29"},
        ],
    })
    r = check_churn(
        ChurnConfig(enabled=True, hot_files_threshold=10,
                    ignore=("tests/**",)),
        backend, "p",
    )
    assert r.status == "passed"
    assert r.actual == 0


# ---- hubs ----------------------------------------------------------

def test_hubs_disabled_is_skipped():
    r = check_hubs(HubsConfig(enabled=False), _FakeBackend(), "p")
    assert r.status == "skipped"


def test_hubs_passes_when_no_hubs_above_threshold():
    backend = _FakeBackend({"_HUBS_": []})
    r = check_hubs(
        HubsConfig(enabled=True, min_fanin=25, max_hubs=0),
        backend, "p",
    )
    assert r.status == "passed"


def test_hubs_fails_when_above_threshold():
    backend = _FakeBackend({"ORDER BY in_callers": [
        {"qualified_name": "pkg.util.normalize", "kind": "function",
         "file": "pkg/util.py", "in_callers": 47, "out_callees": 3},
    ]})
    r = check_hubs(
        HubsConfig(enabled=True, min_fanin=25, max_hubs=0),
        backend, "p",
    )
    assert r.status == "failed"
    assert r.actual == 1
