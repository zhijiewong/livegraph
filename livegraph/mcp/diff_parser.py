"""Parse unified `git diff` text into ``{file_path: set[new_file_lines]}``.

Pure stdlib. Tolerant: unrecognized lines are skipped, never raise on
malformed input. New files (``--- /dev/null``) are tracked; deleted
files (``+++ /dev/null``) are skipped (a documented v1 limitation).
"""
from __future__ import annotations

import re

# Matches a hunk header like ``@@ -A,B +P,Q @@`` or ``@@ -A +P @@`` (count optional).
_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def parse_diff(diff_text: str) -> dict[str, set[int]]:
    """Return a map from file path to the set of new-file line numbers touched.

    The path is normalized to forward slashes. Files added by the diff are
    included. Files deleted by the diff are not (their ``+++`` is ``/dev/null``).
    """
    result: dict[str, set[int]] = {}
    current_file: str | None = None
    skip_block = False
    current_line: int | None = None  # walking counter inside a hunk

    for line in diff_text.splitlines():
        # New file block — captures the path of the file we're tracking.
        if line.startswith("+++ "):
            target = line[len("+++ "):].strip()
            if target == "/dev/null":
                current_file = None
                skip_block = True
            else:
                if target.startswith("b/"):
                    target = target[len("b/"):]
                current_file = target.replace("\\", "/")
                skip_block = False
                result.setdefault(current_file, set())
            current_line = None
            continue

        if line.startswith("--- "):
            continue

        if skip_block or current_file is None:
            continue

        hunk = _HUNK_RE.match(line)
        if hunk is not None:
            current_line = int(hunk.group("new_start"))
            continue

        if current_line is None:
            continue

        # The "no newline at end of file" sentinel does not consume a counter.
        if line.startswith("\\"):
            continue

        if line.startswith("+"):
            result[current_file].add(current_line)
            current_line += 1
        elif line.startswith("-"):
            continue
        elif line.startswith(" ") or line == "":
            # Context line. Real `git diff` uses ' ' for blank context lines,
            # but some patch tools strip trailing whitespace and produce a
            # truly empty line. Either form is treated as context here.
            current_line += 1
        else:
            # ``diff --git ...``, ``index ...``, anything else outside the
            # body: ignore. Do NOT advance the counter.
            continue

    # Drop files where we recorded nothing.
    return {path: lines for path, lines in result.items() if lines}
