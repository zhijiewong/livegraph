"""Extract definitions, imports, and raw calls from a Python AST."""
from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from livegraph.models import Definition, ImportRecord
from livegraph.qualnames import symbol_qid
from livegraph.static.parser import has_errors, parse_source


@dataclass(frozen=True, slots=True)
class RawCall:
    """An unresolved call site: caller is known, callee is just a name."""

    caller_qn: str
    callee_name: str   # the simple/dotted name as written at the call site
    line: int


def extract(
    rel_path: str, source: bytes,
) -> tuple[list[Definition], list[ImportRecord], list[RawCall]]:
    """Extract definitions, imports, and raw calls from one Python file.

    A file with syntax errors yields three empty lists — the caller is
    responsible for still recording the File node with parse_error=True.
    """
    tree = parse_source(source)
    if has_errors(tree):
        return [], [], []

    definitions: list[Definition] = []
    imports: list[ImportRecord] = []
    calls: list[RawCall] = []
    _walk(tree.root_node, source, rel_path, _scope=None, _class=None,
          definitions=definitions, imports=imports, calls=calls)
    return definitions, imports, calls


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _walk(  # noqa: PLR0913 - a focused recursive visitor
    node: Node, source: bytes, rel_path: str,
    _scope: str | None, _class: str | None,
    definitions: list[Definition], imports: list[ImportRecord],
    calls: list[RawCall],
) -> None:
    """Depth-first visitor. ``_scope`` is the enclosing definition's
    qualified_name; ``_class`` is the enclosing class's qualified_name."""
    for child in node.children:
        if child.type == "function_definition":
            _handle_function(child, source, rel_path, _scope, _class,
                             definitions, imports, calls)
        elif child.type == "class_definition":
            _handle_class(child, source, rel_path, _scope,
                          definitions, imports, calls)
        elif child.type in ("import_statement", "import_from_statement"):
            imports.extend(_handle_import(child, source, rel_path))
        elif child.type == "call" and _scope is not None:
            calls.append(_handle_call(child, source, _scope))
            _walk(child, source, rel_path, _scope, _class,
                  definitions, imports, calls)
        else:
            _walk(child, source, rel_path, _scope, _class,
                  definitions, imports, calls)


def _name_of(node: Node, source: bytes) -> str:
    field = node.child_by_field_name("name")
    return _text(field, source) if field is not None else "<anonymous>"


def _decorators(node: Node, source: bytes) -> tuple[str, ...]:
    """Decorator identifiers, if ``node`` sits inside a decorated_definition."""
    parent = node.parent
    if parent is None or parent.type != "decorated_definition":
        return ()
    out: list[str] = []
    for child in parent.children:
        if child.type == "decorator":
            text = _text(child, source).lstrip("@").strip()
            out.append(text.split("(", 1)[0])
    return tuple(out)


def _handle_function(  # noqa: PLR0913
    node: Node, source: bytes, rel_path: str,
    scope: str | None, cls: str | None,
    definitions: list[Definition], imports: list[ImportRecord],
    calls: list[RawCall],
) -> None:
    name = _name_of(node, source)
    dotted = f"{_class_simple(cls)}.{name}" if cls is not None else name
    qn = symbol_qid(rel_path, dotted)
    kind = "method" if cls is not None else "function"
    definitions.append(Definition(
        qualified_name=qn, name=name, kind=kind, file=rel_path,
        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        decorators=_decorators(node, source), source=_text(node, source),
        parent_class=cls,
    ))
    body = node.child_by_field_name("body")
    if body is not None:
        # Nested defs descend with this function as scope but no class.
        _walk(body, source, rel_path, _scope=qn, _class=None,
              definitions=definitions, imports=imports, calls=calls)


def _handle_class(  # noqa: PLR0913
    node: Node, source: bytes, rel_path: str, scope: str | None,
    definitions: list[Definition], imports: list[ImportRecord],
    calls: list[RawCall],
) -> None:
    name = _name_of(node, source)
    qn = symbol_qid(rel_path, name)
    definitions.append(Definition(
        qualified_name=qn, name=name, kind="class", file=rel_path,
        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        decorators=_decorators(node, source), source=_text(node, source),
        parent_class=None,
    ))
    body = node.child_by_field_name("body")
    if body is not None:
        _walk(body, source, rel_path, _scope=qn, _class=qn,
              definitions=definitions, imports=imports, calls=calls)


def _class_simple(class_qn: str) -> str:
    """Return the bare class name from its qualified_name."""
    return class_qn.split("::", 1)[1]


def _handle_import(
    node: Node, source: bytes, rel_path: str,
) -> list[ImportRecord]:
    raw = _text(node, source)
    line = node.start_point[0] + 1
    modules: list[str] = []
    if node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                modules.append(_text(child, source))
            elif child.type == "aliased_import":
                inner = child.child_by_field_name("name")
                if inner is not None:
                    modules.append(_text(inner, source))
    else:  # import_from_statement
        mod = node.child_by_field_name("module_name")
        if mod is not None:
            modules.append(_text(mod, source))
    return [ImportRecord(file=rel_path, raw=raw, line=line, module=m)
            for m in modules]


def _handle_call(node: Node, source: bytes, scope: str) -> RawCall:
    callee = node.child_by_field_name("function")
    name = _text(callee, source) if callee is not None else "<dynamic>"
    return RawCall(caller_qn=scope, callee_name=name,
                   line=node.start_point[0] + 1)
