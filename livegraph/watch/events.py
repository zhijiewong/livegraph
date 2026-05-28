from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

EventKind = Literal["modified", "created", "deleted"]


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    kind: EventKind
    path: Path


@dataclass(frozen=True, slots=True)
class ChangeBatch:
    modified: frozenset[Path] = field(default_factory=frozenset)
    deleted: frozenset[Path] = field(default_factory=frozenset)
    _created: frozenset[Path] = field(default_factory=frozenset)

    @classmethod
    def empty(cls) -> "ChangeBatch":
        return cls()

    def is_empty(self) -> bool:
        return not self.modified and not self.deleted

    def merge(self, event: ChangeEvent) -> "ChangeBatch":
        modified = set(self.modified)
        deleted = set(self.deleted)
        created = set(self._created)
        p = event.path

        if event.kind == "created":
            deleted.discard(p)
            modified.add(p)
            created.add(p)
        elif event.kind == "modified":
            deleted.discard(p)
            modified.add(p)
        elif event.kind == "deleted":
            if p in created:
                modified.discard(p)
                created.discard(p)
            else:
                modified.discard(p)
                deleted.add(p)

        return ChangeBatch(
            modified=frozenset(modified),
            deleted=frozenset(deleted),
            _created=frozenset(created),
        )
