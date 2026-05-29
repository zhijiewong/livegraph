"""Walk a project's git history via subprocess and yield CommitRecords.

We shell out to `git log` and `git diff-tree` rather than depending on a
Python git library. This keeps livegraph's runtime deps minimal and
exactly matches what the user sees from `git` on their command line.
"""
from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

from livegraph.history.models import CommitRecord, FileChange, HunkRange

log = logging.getLogger(__name__)

_LOG_FORMAT = "%H%x1f%h%x1f%aI%x1f%aE%x1f%aN%x1f%s"
_HUNK_HEADER = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _run_git(root: str, args: list[str]) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _split_log_record(raw: bytes) -> tuple[str, str, str, str, str, str]:
    parts = raw.split(b"\x1f", 5)
    if len(parts) != 6:
        raise ValueError(f"malformed git log record: {raw!r}")
    return tuple(p.decode("utf-8", errors="replace") for p in parts)  # type: ignore[return-value]


def _parse_hunks(diff_bytes: bytes) -> list[HunkRange]:
    hunks: list[HunkRange] = []
    for line in diff_bytes.splitlines():
        m = _HUNK_HEADER.match(line)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) else 1
        if count == 0:
            # Pure deletion — no post-image lines. Skip; numstat already
            # records the deletion count.
            continue
        hunks.append(HunkRange(start=start, end=start + count - 1))
    return hunks


def _diff_tree_for(root: str, sha: str, path: str) -> bytes:
    try:
        return _run_git(
            root,
            ["diff-tree", "-p", "--no-renames", "--no-color",
             "--unified=0", sha, "--", path],
        )
    except subprocess.CalledProcessError as exc:
        log.warning("diff-tree failed for %s %s: %s", sha[:8], path,
                    exc.stderr.decode("utf-8", errors="replace"))
        return b""


def _numstat_for(root: str, sha: str) -> list[tuple[int, int, str]]:
    out = _run_git(
        root,
        ["log", "-1", "--first-parent", "--no-merges", "--numstat",
         "--format=", sha],
    )
    rows: list[tuple[int, int, str]] = []
    for line in out.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        adds_s, dels_s, path = parts
        adds = 0 if adds_s == "-" else int(adds_s)
        dels = 0 if dels_s == "-" else int(dels_s)
        rows.append((adds, dels, path))
    return rows


def iter_commits(
    root: str,
    since: str | None = None,
    max_commits: int | None = None,
) -> Iterator[CommitRecord]:
    """Walk first-parent non-merge commits, newest first.

    ``since`` exclusive — pass a SHA and we walk from HEAD back to (but
    not including) that SHA.
    """
    git_dir = Path(root) / ".git"
    if not git_dir.exists():
        raise ValueError(f"not a git repository: {root}")

    log_args = ["log", "--first-parent", "--no-merges",
                f"--pretty=format:{_LOG_FORMAT}"]
    if since:
        log_args.append(f"{since}..HEAD")
    if max_commits is not None:
        log_args.extend(["-n", str(max_commits)])

    log_bytes = _run_git(root, log_args)
    if not log_bytes.strip():
        return
    records = log_bytes.split(b"\n")
    for raw in records:
        if not raw.strip():
            continue
        try:
            sha, short_sha, ts, email, name, message = _split_log_record(raw)
        except ValueError as exc:
            log.warning("skipping unparseable commit: %s", exc)
            continue

        numstat = _numstat_for(root, sha)
        if not numstat:
            continue

        files: list[FileChange] = []
        for adds, dels, path in numstat:
            hunks = tuple(_parse_hunks(_diff_tree_for(root, sha, path)))
            files.append(FileChange(
                path=path, additions=adds, deletions=dels, hunks=hunks,
            ))

        yield CommitRecord(
            sha=sha, short_sha=short_sha, message=message,
            timestamp=ts, author_email=email, author_name=name,
            files=tuple(files),
        )
