"""Stable symbol identity shared by the static and runtime phases.

A symbol's ``qualified_name`` is ``<relative-path>::<dotted-name>``,
e.g. ``src/app/h.py::Handler.run``. Phase 1 builds it from the AST;
Phase 2 builds the identical string from a runtime code object.
"""
from __future__ import annotations

import os


def file_qid(rel: str) -> str:
    """Normalize a relative path to a forward-slash file identifier."""
    return rel.replace("\\", "/")


def symbol_qid(rel: str, dotted_name: str) -> str:
    """Build a symbol qualified_name from its file and dotted name.

    ``dotted_name`` is the AST-nesting path, e.g. ``Handler.run`` for a
    method or ``process`` for a module-level function.
    """
    return f"{file_qid(rel)}::{dotted_name}"


def normalize_co_qualname(co_qualname: str) -> str:
    """Strip CPython ``<locals>`` segments from a code object qualname.

    ``outer.<locals>.inner`` -> ``outer.inner`` so a runtime qualname
    lines up with the dotted name Phase 1 derives from AST nesting.
    """
    return ".".join(part for part in co_qualname.split(".") if part != "<locals>")


def rel_path(abs_path: str, root: str) -> str | None:
    """Return ``abs_path`` relative to ``root`` (forward slashes), or None.

    None means the path is outside the project root and should be ignored.
    """
    try:
        rel = os.path.relpath(os.path.realpath(abs_path), os.path.realpath(root))
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return file_qid(rel)
