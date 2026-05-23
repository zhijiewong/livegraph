"""Discover Python source files under a project root."""
from __future__ import annotations

import os
from collections.abc import Iterator

_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env", ".tox", "build", "dist",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "node_modules",
}


def discover_python_files(root: str) -> Iterator[str]:
    """Yield project-relative, forward-slash paths of every ``.py`` file."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".py"):
                abs_path = os.path.join(dirpath, filename)
                yield os.path.relpath(abs_path, root).replace("\\", "/")
