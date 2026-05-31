from __future__ import annotations

from livegraph.static_ts.parser import has_errors, parse_source


def test_parses_basic_typescript_function():
    src = b"function foo(): number { return 1; }\n"
    tree = parse_source(src, jsx=False)
    assert not has_errors(tree)


def test_parses_class_with_method():
    src = b"class C { m(): void {} }\n"
    tree = parse_source(src, jsx=False)
    assert not has_errors(tree)


def test_parses_tsx_jsx_syntax():
    src = b"const x = <div>hi</div>;\n"
    tree = parse_source(src, jsx=True)
    assert not has_errors(tree)


def test_typescript_grammar_does_not_parse_jsx():
    src = b"const x = <div>hi</div>;\n"
    tree = parse_source(src, jsx=False)
    assert has_errors(tree)


def test_syntax_error_reported():
    src = b"function (: { broken"
    tree = parse_source(src, jsx=False)
    assert has_errors(tree)
