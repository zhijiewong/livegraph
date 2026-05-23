"""tree-sitter parsing for Python source."""
from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Tree

PY_LANGUAGE = Language(tspython.language())
_PARSER = Parser(PY_LANGUAGE)


def parse_source(source: bytes) -> Tree:
    """Parse Python ``source`` bytes into a tree-sitter ``Tree``.

    Never raises on malformed input — tree-sitter produces a tree with
    ERROR nodes instead. Use ``has_errors`` to detect that.
    """
    return _PARSER.parse(source)


def has_errors(tree: Tree) -> bool:
    """Return True if the parse tree contains syntax errors."""
    return tree.root_node.has_error
