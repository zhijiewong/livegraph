from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from pathlib import Path

_BUILTIN_IGNORES = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


class PathFilter:
    """Decides whether a path should be watched.

    Filters out:
      * files that are not `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, or `.cjs`
      * paths outside `root`
      * any path that has a builtin-ignore segment
      * gitignore-style patterns from `root/.gitignore` (if present)
      * user-supplied globs
    """

    def __init__(
        self,
        root: Path,
        *,
        user_ignores: Iterable[str] = (),
    ) -> None:
        self._root = root.resolve()
        self._user_ignores = tuple(user_ignores)
        self._gitignore = _load_gitignore(self._root)

    def accepts(self, path: Path) -> bool:
        try:
            rel = path.resolve().relative_to(self._root)
        except ValueError:
            return False
        if path.suffix not in (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            return False
        parts = rel.parts
        if any(seg in _BUILTIN_IGNORES for seg in parts):
            return False
        rel_str = str(rel)
        for pat in self._user_ignores:
            if fnmatch.fnmatch(rel_str, pat):
                return False
            if any(fnmatch.fnmatch(seg, pat.rstrip("/")) for seg in parts):
                return False
        for pat in self._gitignore:
            if _gitignore_match(pat, rel_str, parts):
                return False
        return True


def _load_gitignore(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    out: list[str] = []
    for line in gi.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def _gitignore_match(pat: str, rel_str: str, parts: tuple[str, ...]) -> bool:
    if pat.endswith("/"):
        return pat.rstrip("/") in parts
    if fnmatch.fnmatch(rel_str, pat):
        return True
    return any(fnmatch.fnmatch(seg, pat) for seg in parts)
