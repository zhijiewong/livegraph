"""Watch loop orchestrator.

Pulls ChangeBatches off the debouncer, drives update_files (and
optionally embed_project), with parse-vs-backend error classification
and exponential backoff for backend failures.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from livegraph.incremental import UpdateSummary

log = logging.getLogger(__name__)

_BACKOFF_START = 1.0
_BACKOFF_CAP = 30.0


def _never_stop() -> bool:
    return False


@dataclass(frozen=True)
class LoopDeps:
    backend: Any
    project: str
    root: str
    debouncer: Any
    update_files: Callable[..., UpdateSummary]
    embed_project: Callable[..., Any] | None
    provider: Any | None
    sleep: Callable[[float], None] = field(default=time.sleep)
    should_stop: Callable[[], bool] = field(default=_never_stop)


def run_loop(deps: LoopDeps) -> None:
    backoff = _BACKOFF_START
    while not deps.should_stop():
        batch = deps.debouncer.next_batch(timeout=1.0)
        if batch is None:
            continue
        if hasattr(batch, "is_empty") and batch.is_empty():
            continue

        paths: list[str] = []
        for p in (*batch.modified, *batch.deleted):
            sp = str(p)
            if sp.startswith("/"):
                paths.append(sp)
            else:
                paths.append(f"{deps.root}/{sp}")

        try:
            summary = deps.update_files(
                deps.root, deps.backend, deps.project, paths=paths,
            )
        except ConnectionError as e:
            log.error("backend error: %s; backing off %.1fs", e, backoff)
            deps.sleep(backoff)
            backoff = min(backoff * 2.0, _BACKOFF_CAP)
            continue
        except Exception:
            log.exception("update_files crashed; continuing")
            continue

        backoff = _BACKOFF_START
        if summary is not None and getattr(summary, "parse_errors", 0):
            log.info("parse errors in batch: %d (continuing)",
                     summary.parse_errors)
            continue

        if deps.embed_project is not None and deps.provider is not None:
            try:
                deps.embed_project(deps.backend, deps.project, deps.provider)
            except Exception as e:
                log.warning("embed step failed (continuing): %s", e)
