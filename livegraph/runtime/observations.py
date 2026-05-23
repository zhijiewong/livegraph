"""Map live CPython code objects to livegraph qualified_names."""
from __future__ import annotations

import types

from livegraph.qualnames import normalize_co_qualname, rel_path, symbol_qid


def qid_from_code(code: types.CodeType, root: str) -> str | None:
    """Return the qualified_name for a code object, or None.

    None means the code is outside ``root`` (stdlib, third-party) or
    otherwise cannot be mapped — callers log and skip such frames.
    """
    rel = rel_path(code.co_filename, root)
    if rel is None:
        return None
    dotted = normalize_co_qualname(code.co_qualname)
    if not dotted or "<" in dotted:
        # Module bodies, comprehensions, lambdas — not v1 mappable.
        return None
    return symbol_qid(rel, dotted)
