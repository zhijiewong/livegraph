"""tree-sitter parsing for TypeScript / JavaScript source."""
from __future__ import annotations

import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser, Tree

TS_LANGUAGE = Language(tsts.language_typescript())
TSX_LANGUAGE = Language(tsts.language_tsx())

_TS_PARSER = Parser(TS_LANGUAGE)
_TSX_PARSER = Parser(TSX_LANGUAGE)


def parse_source(source: bytes, *, jsx: bool = False) -> Tree:
    """Parse TS/JS ``source`` bytes. ``jsx=True`` for ``.tsx``/``.jsx``.

    Never raises on malformed input — tree-sitter produces a tree with
    ERROR nodes instead. Use ``has_errors`` to detect that.
    """
    parser = _TSX_PARSER if jsx else _TS_PARSER
    return parser.parse(source)


def has_errors(tree: Tree) -> bool:
    """Return True if the parse tree contains syntax errors."""
    return tree.root_node.has_error


def is_jsx_file(rel_path: str) -> bool:
    """Pick the grammar for a file by extension. .tsx and .jsx use JSX."""
    return rel_path.endswith(".tsx") or rel_path.endswith(".jsx")
