from __future__ import annotations

import queue
from pathlib import Path

from livegraph.watch.debouncer import Debouncer
from livegraph.watch.events import ChangeEvent


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_empty_queue_returns_none_after_timeout():
    q: queue.Queue[ChangeEvent] = queue.Queue()
    clock = FakeClock()
    d = Debouncer(q, window_ms=300, clock=clock.now, sleep=lambda dt: clock.advance(dt))
    assert d.next_batch(timeout=0.05) is None


def test_single_event_returned_after_window_elapses():
    q: queue.Queue[ChangeEvent] = queue.Queue()
    clock = FakeClock()
    d = Debouncer(q, window_ms=300, clock=clock.now, sleep=lambda dt: clock.advance(dt))
    q.put(ChangeEvent(kind="modified", path=Path("a.py")))
    batch = d.next_batch(timeout=1.0)
    assert batch is not None
    assert batch.modified == {Path("a.py")}


def test_burst_of_events_coalesces_into_one_batch():
    q: queue.Queue[ChangeEvent] = queue.Queue()
    clock = FakeClock()
    d = Debouncer(q, window_ms=300, clock=clock.now, sleep=lambda dt: clock.advance(dt))
    q.put(ChangeEvent(kind="modified", path=Path("a.py")))
    q.put(ChangeEvent(kind="modified", path=Path("b.py")))
    q.put(ChangeEvent(kind="deleted", path=Path("c.py")))
    batch = d.next_batch(timeout=1.0)
    assert batch is not None
    assert batch.modified == {Path("a.py"), Path("b.py")}
    assert batch.deleted == {Path("c.py")}
