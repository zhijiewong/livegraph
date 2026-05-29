"""Data classes for the git-history layer."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class HunkRange:
    """A single post-image hunk range from `git diff-tree`.

    `start` and `end` are inclusive 1-based line numbers in the file as
    it exists at the commit (the "after" side of the diff).
    """
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class FileChange:
    """One file's changes in a single commit."""
    path: str
    additions: int
    deletions: int
    hunks: tuple[HunkRange, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CommitRecord:
    """One commit with all its file changes."""
    sha: str
    short_sha: str
    message: str
    timestamp: str        # ISO-8601, sortable
    author_email: str
    author_name: str
    files: tuple[FileChange, ...] = field(default_factory=tuple)
