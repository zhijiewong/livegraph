from __future__ import annotations

from livegraph.static_ts.extractor import extract


def test_top_level_function_declaration():
    src = b"export function foo(): number { return 1; }\n"
    defs, imports, calls = extract("src/m.ts", src)
    qns = [d.qualified_name for d in defs]
    assert "src/m.ts::foo" in qns
    foo = next(d for d in defs if d.qualified_name == "src/m.ts::foo")
    assert foo.kind == "function"
    assert foo.name == "foo"


def test_arrow_function_assigned_to_const():
    src = b"const greet = () => 'hi';\n"
    defs, _imports, _calls = extract("src/g.ts", src)
    qns = [d.qualified_name for d in defs]
    assert "src/g.ts::greet" in qns


def test_class_with_method():
    src = b"class C {\n  m(): void {}\n}\n"
    defs, _imports, _calls = extract("src/c.ts", src)
    qns = {d.qualified_name for d in defs}
    assert "src/c.ts::C" in qns
    assert "src/c.ts::C.m" in qns
    method = next(d for d in defs if d.qualified_name == "src/c.ts::C.m")
    assert method.kind == "method"
    assert method.parent_class == "src/c.ts::C"


def test_default_export_function_named_default():
    src = b"export default function foo() { return 1; }\n"
    defs, _imports, _calls = extract("src/d.ts", src)
    qns = {d.qualified_name for d in defs}
    assert "src/d.ts::default" in qns


def test_es_module_named_import():
    src = b'import { Calc } from "./calc";\n'
    _defs, imports, _calls = extract("src/m.ts", src)
    assert len(imports) >= 1
    imp = imports[0]
    assert imp.file == "src/m.ts"
    assert imp.module == "./calc"
    assert imp.line == 1


def test_namespace_import():
    src = b'import * as ns from "./util";\n'
    _defs, imports, _calls = extract("src/m.ts", src)
    assert any(i.module == "./util" for i in imports)


def test_direct_call_captured():
    src = b"function foo() { bar(); }\n"
    _defs, _imports, calls = extract("src/m.ts", src)
    assert any(
        c.caller_qn == "src/m.ts::foo" and c.callee_name == "bar"
        for c in calls
    )


def test_method_call_uses_method_name():
    src = b"function f() { obj.m(); }\n"
    _defs, _imports, calls = extract("src/m.ts", src)
    assert any(
        c.caller_qn == "src/m.ts::f" and c.callee_name == "m"
        for c in calls
    )


def test_new_class_captured_as_call():
    src = b"function f() { new C(); }\n"
    _defs, _imports, calls = extract("src/m.ts", src)
    assert any(
        c.caller_qn == "src/m.ts::f" and c.callee_name == "C"
        for c in calls
    )


def test_syntax_error_yields_empty_lists():
    src = b"function broken (: { fail"
    defs, imports, calls = extract("src/m.ts", src)
    assert defs == [] and imports == [] and calls == []
