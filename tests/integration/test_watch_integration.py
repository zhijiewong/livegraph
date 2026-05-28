"""End-to-end: watchdog + Debouncer + Loop + real Neo4j."""
from __future__ import annotations

import queue
import threading
import time

import pytest

from livegraph.incremental import update_files
from livegraph.watch.debouncer import Debouncer
from livegraph.watch.events import ChangeEvent
from livegraph.watch.loop import LoopDeps, run_loop
from livegraph.watch.watcher import Watcher

pytestmark = pytest.mark.integration


def _wait_for(predicate, timeout=5.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _start_watch(backend, project, root):
    events: queue.Queue[ChangeEvent] = queue.Queue()
    watcher = Watcher(root=root, events=events)
    debouncer = Debouncer(events, window_ms=100)
    stop_flag = {"stop": False}
    deps = LoopDeps(
        backend=backend, project=project, root=str(root),
        debouncer=debouncer, update_files=update_files,
        embed_project=None, provider=None,
        should_stop=lambda: stop_flag["stop"],
    )
    watcher.start()
    t = threading.Thread(target=run_loop, args=(deps,), daemon=True)
    t.start()
    return watcher, t, stop_flag


def _stop_watch(watcher, t, stop_flag):
    stop_flag["stop"] = True
    watcher.stop()
    t.join(timeout=5.0)


def test_watch_picks_up_new_file(neo4j_backend, tmp_path):
    backend = neo4j_backend
    project = "watch_test"
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "a.py").write_text("def foo():\n    return 1\n")

    from livegraph.ingest import ingest_project
    ingest_project(str(tmp_path), backend, project_name=project)

    watcher, t, stop_flag = _start_watch(backend, project, tmp_path)
    try:
        (tmp_path / "pkg" / "b.py").write_text("def bar():\n    return 2\n")
        ok = _wait_for(lambda: bool(backend.execute(
            "MATCH (:Project {name: $p})-[:CONTAINS]->"
            "(f:File {path: 'pkg/b.py'}) RETURN f LIMIT 1",
            p=project,
        )))
        assert ok, "new file did not appear in graph within timeout"
    finally:
        _stop_watch(watcher, t, stop_flag)


def test_watch_picks_up_modification_and_deletion(neo4j_backend, tmp_path):
    backend = neo4j_backend
    project = "watch_test_mod"
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    target = tmp_path / "pkg" / "a.py"
    target.write_text("def foo():\n    return 1\n")

    from livegraph.ingest import ingest_project
    ingest_project(str(tmp_path), backend, project_name=project)

    watcher, t, stop_flag = _start_watch(backend, project, tmp_path)
    try:
        target.write_text(
            "def foo():\n    return 99\n\ndef brand_new():\n    return 7\n"
        )
        ok = _wait_for(lambda: bool(backend.execute(
            "MATCH (s:Function {name: 'brand_new'}) RETURN s LIMIT 1",
        )))
        assert ok, "modification not reflected in graph"

        target.unlink()
        ok = _wait_for(lambda: not backend.execute(
            "MATCH (:Project {name: $p})-[:CONTAINS]->"
            "(f:File {path: 'pkg/a.py'}) RETURN f LIMIT 1",
            p=project,
        ))
        assert ok, "deleted file still present in graph"
    finally:
        _stop_watch(watcher, t, stop_flag)
