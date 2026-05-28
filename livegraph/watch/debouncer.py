from __future__ import annotations

import queue
import time
from collections.abc import Callable

from livegraph.watch.events import ChangeBatch, ChangeEvent


class Debouncer:
    """Coalesce a burst of ChangeEvents into a single ChangeBatch.

    next_batch(timeout) blocks until the queue has been quiet for
    window_ms after the first event in a burst, or returns None if no
    event arrived within timeout seconds.
    """

    def __init__(
        self,
        events: queue.Queue[ChangeEvent],
        window_ms: int = 300,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._events = events
        self._window = window_ms / 1000.0
        self._clock = clock
        self._sleep = sleep

    def next_batch(self, timeout: float = 1.0) -> ChangeBatch | None:
        try:
            first = self._events.get(timeout=timeout)
        except queue.Empty:
            return None

        batch = ChangeBatch.empty().merge(first)
        deadline = self._clock() + self._window
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            try:
                ev = self._events.get(timeout=remaining)
            except queue.Empty:
                break
            batch = batch.merge(ev)
            deadline = self._clock() + self._window
        return batch if not batch.is_empty() else ChangeBatch.empty()
