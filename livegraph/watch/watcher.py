"""watchdog-based file system watcher.

Translates raw watchdog events into ChangeEvents and pushes them onto a
queue for the Debouncer to consume.
"""
from __future__ import annotations

import logging
import queue
from collections.abc import Iterable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from livegraph.watch.events import ChangeEvent
from livegraph.watch.filters import PathFilter

log = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, q: queue.Queue[ChangeEvent], pf: PathFilter) -> None:
        self._q = q
        self._pf = pf

    def _emit(self, kind: str, raw_path: str) -> None:
        p = Path(raw_path)
        if not self._pf.accepts(p):
            return
        self._q.put(ChangeEvent(kind=kind, path=p))  # type: ignore[arg-type]

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._emit("created", event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._emit("modified", event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._emit("deleted", event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._emit("deleted", event.src_path)
        dest = getattr(event, "dest_path", None)
        if dest:
            self._emit("created", dest)


class Watcher:
    """Owns a watchdog Observer and pushes ChangeEvents onto a queue."""

    def __init__(
        self,
        root: Path,
        events: queue.Queue[ChangeEvent],
        *,
        user_ignores: Iterable[str] = (),
    ) -> None:
        self._root = root
        self._q = events
        self._pf = PathFilter(root=root, user_ignores=user_ignores)
        self._observer = Observer()

    def start(self) -> None:
        handler = _Handler(self._q, self._pf)
        self._observer.schedule(handler, str(self._root), recursive=True)
        self._observer.start()
        log.info("watching %s", self._root)

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=5.0)
