"""Extract definitions, imports, and raw calls from a TS/JS AST."""
from __future__ import annotations

from tree_sitter import Node

from livegraph.models import Definition, ImportRecord
from livegraph.qualnames import symbol_qid
from livegraph.static.extractor import RawCall  # reuse the Python dataclass
from livegraph.static_ts.parser import (
    has_errors, is_jsx_file, parse_source,
)


def extract(
    rel_path: str, source: bytes,
) -> tuple[list[Definition], list[ImportRecord], list[RawCall]]:
    """Extract definitions/imports/calls from one TS/JS file."""
    tree = parse_source(source, jsx=is_jsx_file(rel_path))
    if has_errors(tree):
        return [], [], []

    definitions: list[Definition] = []
    imports: list[ImportRecord] = []
    calls: list[RawCall] = []
    _walk(tree.root_node, source, rel_path,
          scope=None, class_qn=None,
          definitions=definitions, imports=imports, calls=calls)
    return definitions, imports, calls


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _walk(
    node: Node, source: bytes, rel_path: str,
    scope: str | None, class_qn: str | None,
    definitions: list[Definition], imports: list[ImportRecord],
    calls: list[RawCall],
) -> None:
    nt = node.type

    # ---- imports ---------------------------------------------------
    if nt == "import_statement":
        module = _import_source(node, source)
        if module is not None:
            raw = _text(node, source)
            imports.append(ImportRecord(
                file=rel_path, module=module, raw=raw,
                line=node.start_point[0] + 1,
            ))
        return

    # ---- function declaration -------------------------------------
    if nt == "function_declaration":
        name = _identifier_child(node, source) or "(anonymous)"
        qn = symbol_qid(rel_path, name)
        definitions.append(_def(
            qn, "function", name, rel_path, node, source,
            parent_class=None,
        ))
        _walk_children(node, source, rel_path, qn, None,
                       definitions, imports, calls)
        return

    # ---- variable declaration that holds an arrow / function expr --
    if nt in ("lexical_declaration", "variable_declaration"):
        for declarator in _children_of_type(node, "variable_declarator"):
            init = declarator.child_by_field_name("value")
            if init is not None and init.type in (
                "arrow_function", "function_expression",
            ):
                ident = declarator.child_by_field_name("name")
                if ident is not None and ident.type == "identifier":
                    name = _text(ident, source)
                    qn = symbol_qid(rel_path, name)
                    definitions.append(_def(
                        qn, "function", name, rel_path, node, source,
                        parent_class=None,
                    ))
                    _walk_children(init, source, rel_path, qn, None,
                                   definitions, imports, calls)
            else:
                # Non-function init (e.g. call expression, object literal):
                # recurse to capture any call expressions inside it.
                if init is not None:
                    _walk(init, source, rel_path, scope, class_qn,
                          definitions, imports, calls)
        return

    # ---- export default function (named or anonymous) --------------
    if nt == "export_statement":
        default_kw = any(c.type == "default" for c in node.children)
        if default_kw:
            # The function may live under the "declaration" field.
            decl = node.child_by_field_name("declaration")
            if decl is None:
                # Fall back: scan direct children for function-like nodes.
                for child in node.children:
                    if child.type in (
                        "function_declaration", "arrow_function",
                        "function_expression",
                    ):
                        decl = child
                        break
            if decl is not None and decl.type in (
                "function_declaration", "arrow_function",
                "function_expression",
            ):
                qn = symbol_qid(rel_path, "default")
                definitions.append(_def(
                    qn, "function", "default", rel_path, node, source,
                    parent_class=None,
                ))
                _walk_children(decl, source, rel_path, qn, None,
                               definitions, imports, calls)
                return
        # Non-default export or no recognisable function: recurse normally.
        _walk_children(node, source, rel_path, scope, class_qn,
                       definitions, imports, calls)
        return

    # ---- class -----------------------------------------------------
    if nt == "class_declaration":
        name = _identifier_child(node, source) or "(anonymous)"
        qn = symbol_qid(rel_path, name)
        definitions.append(_def(
            qn, "class", name, rel_path, node, source,
            parent_class=None,
        ))
        body = node.child_by_field_name("body")
        if body is not None:
            for member in body.children:
                if member.type == "method_definition":
                    mname = _method_name(member, source)
                    if mname is None:
                        continue
                    mqn = symbol_qid(rel_path, f"{name}.{mname}")
                    definitions.append(_def(
                        mqn, "method", mname, rel_path, member, source,
                        parent_class=qn,
                    ))
                    _walk_children(member, source, rel_path, mqn, qn,
                                   definitions, imports, calls)
        return

    # ---- call expression (including new C()) ----------------------
    if nt in ("call_expression", "new_expression"):
        callee_name = _call_target_name(node, source)
        if callee_name and scope is not None:
            calls.append(RawCall(
                caller_qn=scope, callee_name=callee_name,
                line=node.start_point[0] + 1,
            ))
        # Fall through to recurse into arguments / nested calls.

    _walk_children(node, source, rel_path, scope, class_qn,
                   definitions, imports, calls)


def _walk_children(
    node: Node, source: bytes, rel_path: str,
    scope: str | None, class_qn: str | None,
    definitions: list[Definition], imports: list[ImportRecord],
    calls: list[RawCall],
) -> None:
    for child in node.children:
        _walk(child, source, rel_path, scope, class_qn,
              definitions, imports, calls)


def _def(
    qn: str, kind: str, name: str, rel_path: str,
    node: Node, source: bytes, parent_class: str | None,
) -> Definition:
    return Definition(
        qualified_name=qn,
        kind=kind,
        name=name,
        file=rel_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        decorators=(),
        source=_text(node, source),
        parent_class=parent_class,
    )


def _identifier_child(node: Node, source: bytes) -> str | None:
    ident = node.child_by_field_name("name")
    if ident is not None and ident.type in ("identifier", "type_identifier"):
        return _text(ident, source)
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return _text(child, source)
    return None


def _method_name(member: Node, source: bytes) -> str | None:
    name_node = member.child_by_field_name("name")
    if name_node is None:
        return None
    return _text(name_node, source)


def _children_of_type(node: Node, type_: str) -> list[Node]:
    return [c for c in node.children if c.type == type_]


def _import_source(node: Node, source: bytes) -> str | None:
    src_node = node.child_by_field_name("source")
    if src_node is None:
        for child in node.children:
            if child.type == "string":
                src_node = child
                break
    if src_node is None:
        return None
    raw = _text(src_node, source).strip()
    if (raw.startswith('"') and raw.endswith('"')) or \
       (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    return raw


def _call_target_name(node: Node, source: bytes) -> str | None:
    """Get the simple name of a call's callee.

    ``foo()`` -> ``foo``; ``obj.method()`` -> ``method``;
    ``new C()`` -> ``C``; chained / computed -> None.
    """
    func = node.child_by_field_name("function") or \
           node.child_by_field_name("constructor")
    if func is None:
        for child in node.children:
            if child.type in ("identifier", "member_expression"):
                func = child
                break
    if func is None:
        return None
    if func.type == "identifier":
        return _text(func, source)
    if func.type == "member_expression":
        prop = func.child_by_field_name("property")
        if prop is not None and prop.type in (
            "property_identifier", "identifier",
        ):
            return _text(prop, source)
    return None
