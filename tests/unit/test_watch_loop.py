from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from livegraph.incremental import UpdateSummary
from livegraph.watch.events import ChangeBatch
from livegraph.watch.loop import LoopDeps, run_loop


class FakeDebouncer:
    """Returns canned batches in order, then ChangeBatch.empty() forever."""

    def __init__(self, batches):
        self._batches = list(batches)

    def next_batch(self, timeout):  # noqa: ARG002
        if self._batches:
            return self._batches.pop(0)
        return ChangeBatch.empty()


def stop_after(n_calls):
    counter = {"n": 0}

    def predicate():
        counter["n"] += 1
        return counter["n"] > n_calls
    return predicate


def _ok_summary():
    return UpdateSummary(0, 0, 0, 0, 0)


def test_loop_calls_update_files_per_batch():
    batch = ChangeBatch(modified=frozenset({Path("a.py")}), deleted=frozenset())
    update_files = MagicMock(return_value=_ok_summary())
    deps = LoopDeps(
        backend=MagicMock(),
        project="proj",
        root="/tmp/proj",
        debouncer=FakeDebouncer([batch, batch]),
        update_files=update_files,
        embed_project=None,
        provider=None,
        sleep=lambda dt: None,
        should_stop=stop_after(2),
    )
    run_loop(deps)
    assert update_files.call_count == 2


def test_loop_calls_embed_when_provider_set():
    batch = ChangeBatch(modified=frozenset({Path("a.py")}), deleted=frozenset())
    update_files = MagicMock(return_value=_ok_summary())
    embed_project = MagicMock()
    deps = LoopDeps(
        backend=MagicMock(),
        project="proj",
        root="/tmp/proj",
        debouncer=FakeDebouncer([batch]),
        update_files=update_files,
        embed_project=embed_project,
        provider=MagicMock(),
        sleep=lambda dt: None,
        should_stop=stop_after(1),
    )
    run_loop(deps)
    embed_project.assert_called_once()


def test_loop_skips_embed_on_parse_errors_no_backoff():
    batch = ChangeBatch(modified=frozenset({Path("a.py")}), deleted=frozenset())
    update_files = MagicMock(return_value=UpdateSummary(0, 0, 0, 0, 1))
    embed_project = MagicMock()
    sleeps = []
    deps = LoopDeps(
        backend=MagicMock(),
        project="proj",
        root="/tmp/proj",
        debouncer=FakeDebouncer([batch]),
        update_files=update_files,
        embed_project=embed_project,
        provider=MagicMock(),
        sleep=lambda dt: sleeps.append(dt),
        should_stop=stop_after(1),
    )
    run_loop(deps)
    embed_project.assert_not_called()
    assert sleeps == []  # no backoff for parse errors


def test_loop_backs_off_on_backend_error():
    batch = ChangeBatch(modified=frozenset({Path("a.py")}), deleted=frozenset())
    calls = {"n": 0}

    def flaky_update(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("neo4j down")
        return _ok_summary()

    sleeps = []
    deps = LoopDeps(
        backend=MagicMock(),
        project="proj",
        root="/tmp/proj",
        debouncer=FakeDebouncer([batch, batch]),
        update_files=flaky_update,
        embed_project=None,
        provider=None,
        sleep=lambda dt: sleeps.append(dt),
        should_stop=stop_after(2),
    )
    run_loop(deps)
    assert sleeps and sleeps[0] >= 1.0


def test_loop_backs_off_on_neo4j_service_unavailable():
    """Driver-level ServiceUnavailable must trigger backoff, not the
    silent generic-Exception path. Regression test for the case where
    Neo4j goes down mid-watch."""
    from neo4j.exceptions import ServiceUnavailable

    batch = ChangeBatch(modified=frozenset({Path("a.py")}), deleted=frozenset())
    calls = {"n": 0}

    def flaky_update(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ServiceUnavailable("connection refused")
        return _ok_summary()

    sleeps = []
    deps = LoopDeps(
        backend=MagicMock(),
        project="proj",
        root="/tmp/proj",
        debouncer=FakeDebouncer([batch, batch]),
        update_files=flaky_update,
        embed_project=None,
        provider=None,
        sleep=lambda dt: sleeps.append(dt),
        should_stop=stop_after(2),
    )
    run_loop(deps)
    assert sleeps and sleeps[0] >= 1.0


def test_loop_skips_empty_batches_without_calling_update():
    update_files = MagicMock(return_value=_ok_summary())
    deps = LoopDeps(
        backend=MagicMock(),
        project="proj",
        root="/tmp/proj",
        debouncer=FakeDebouncer([]),  # always returns ChangeBatch.empty()
        update_files=update_files,
        embed_project=None,
        provider=None,
        sleep=lambda dt: None,
        should_stop=stop_after(3),
    )
    run_loop(deps)
    update_files.assert_not_called()
