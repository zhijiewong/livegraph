# livegraph Phase 13 — TypeScript / JavaScript support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make livegraph multi-language by adding a parallel `livegraph/static_ts/` pipeline (tree-sitter-typescript) that ingests `.ts`/`.tsx`/`.js`/`.jsx`/`.mjs`/`.cjs` into the same Neo4j schema. `livegraph build` auto-detects which pipelines to run; all downstream tools (writer, MCP, watch, history, check, embeddings) work without changes.

**Architecture:** Per-language modules under one shared schema (no `LanguageBackend` Protocol — YAGNI until a 3rd language arrives). New `livegraph/static_ts/` mirrors `livegraph/static/` shape one-for-one (parser/extractor/resolver/qualnames/tsconfig). `ingest_project` walks the project once, groups files by extension, dispatches to both pipelines, merges into one writer transaction.

**Tech Stack:** Python 3.12+, `tree-sitter-typescript>=0.23` (new runtime dep), existing Neo4j backend, existing Typer CLI.

---

## Important context

The existing Python pipeline already uses **file-prefixed qnames** via `livegraph/qualnames.py:symbol_qid(rel, dotted)` → `"src/app/h.py::Handler.run"`. **TS reuses the exact same helper** — no new qname scheme needed. Spec section "Qualified-name scheme" describes a format that already exists; we just point TS at `symbol_qid`.

The existing models in `livegraph/models.py` (`Definition`, `ImportRecord`, `RawCall`, etc.) are language-agnostic — TS extractor returns the same shapes Python's does. `GraphWriter` doesn't care which language produced the rows.

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add `tree-sitter-typescript>=0.23` runtime dep. |
| `livegraph/static_ts/__init__.py` | Create | Package marker. |
| `livegraph/static_ts/parser.py` | Create | Picks `typescript` vs `tsx` grammar by extension; `parse_source(bytes, jsx=False) -> Tree`. |
| `livegraph/static_ts/tsconfig.py` | Create | Tiny `tsconfig.json` reader; resolves `paths`/`baseUrl`. Returns empty struct on missing/malformed file. |
| `livegraph/static_ts/extractor.py` | Create | AST walker → `(definitions, imports, raw_calls)`; same return shape as Python `extract`. |
| `livegraph/static_ts/resolver.py` | Create | `resolve_imports` + `resolve_calls` for TS — relative-path resolution with extension inference, tsconfig alias substitution, bare-module passthrough. |
| `livegraph/discovery.py` | Modify | Add `discover_typescript_files(root)`; existing `discover_python_files` and `_SKIP_DIRS` unchanged. |
| `livegraph/ingest.py` | Modify | `ingest_project` runs both pipelines, merged into one writer. Adds `lang` filter. |
| `livegraph/incremental.py` | Modify | `detect_changes` + `reingest_files` recognize TS extensions and route to the TS extract+resolve path. |
| `livegraph/watch/filters.py` | Modify | `PathFilter` accepts TS extensions in addition to `.py`. |
| `livegraph/cli.py` | Modify | Add `--lang python|typescript|auto` opt-in to `build` / `ingest` / `update`. Default `auto`. |
| `tests/fixtures/sample_project_ts/` | Create | Small TS project: 4 files + tsconfig.json. |
| `tests/unit/test_static_ts_parser.py` | Create | Parser smoke + grammar selection. |
| `tests/unit/test_static_ts_extractor.py` | Create | Definitions, calls, imports extraction. |
| `tests/unit/test_static_ts_tsconfig.py` | Create | tsconfig reader (present, absent, malformed). |
| `tests/unit/test_static_ts_resolver.py` | Create | Relative + alias + bare-module resolution; call name matching. |
| `tests/unit/test_discovery_ts.py` | Create | `discover_typescript_files` picks the right extensions and respects skip-dirs. |
| `tests/unit/test_ingest_ts_dispatch.py` | Create | Auto-detect dispatch in `ingest_project`. |
| `tests/unit/test_watch_filters.py` | Modify | Extend with TS-extension cases. |
| `tests/unit/test_cli.py` | Modify | Add `--lang` flag test for `build`. |
| `tests/integration/test_typescript_integration.py` | Create | Real Neo4j: ingest sample TS project; verify shape. |
| `README.md` | Modify | "TypeScript / JavaScript" section. |

---

## Task 1: Dependency + parser

**Files:**
- Modify: `pyproject.toml`
- Create: `livegraph/static_ts/__init__.py`, `livegraph/static_ts/parser.py`
- Test: `tests/unit/test_static_ts_parser.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_static_ts_parser.py`:

```python
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
    # When jsx=False the typescript grammar should report errors on JSX.
    src = b"const x = <div>hi</div>;\n"
    tree = parse_source(src, jsx=False)
    assert has_errors(tree)


def test_syntax_error_reported():
    src = b"function (: { broken"
    tree = parse_source(src, jsx=False)
    assert has_errors(tree)
```

- [ ] **Step 2: Run, expect collection error (missing module)**

```
.venv/bin/python -m pytest tests/unit/test_static_ts_parser.py -v
```

- [ ] **Step 3: Add the dependency**

In `pyproject.toml`, in the `[project] dependencies = [...]` list, add:

```
"tree-sitter-typescript>=0.23",
```

Then install:

```
.venv/bin/pip install -e '.[dev]'
```

(Or `uv sync` if available — `uv` is not installed in this environment, so use pip.)

- [ ] **Step 4: Create the package + parser**

Create `livegraph/static_ts/__init__.py`:

```python
```

Create `livegraph/static_ts/parser.py`:

```python
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
```

- [ ] **Step 5: Run tests, expect 5 PASS**

```
.venv/bin/python -m pytest tests/unit/test_static_ts_parser.py -v
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml livegraph/static_ts/__init__.py livegraph/static_ts/parser.py tests/unit/test_static_ts_parser.py
git commit -m "feat(phase13): tree-sitter-typescript dep + TS/TSX parser"
```

---

## Task 2: tsconfig.json reader

**Files:**
- Create: `livegraph/static_ts/tsconfig.py`
- Test: `tests/unit/test_static_ts_tsconfig.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path

from livegraph.static_ts.tsconfig import TsConfig, load_tsconfig


def test_no_file_returns_empty(tmp_path):
    cfg = load_tsconfig(str(tmp_path))
    assert isinstance(cfg, TsConfig)
    assert cfg.base_url is None
    assert cfg.paths == {}


def test_baseurl_and_paths_parsed(tmp_path):
    (tmp_path / "tsconfig.json").write_text("""
{
  "compilerOptions": {
    "baseUrl": "./src",
    "paths": {
      "@/util": ["util.ts"],
      "@/calc/*": ["calc/*"]
    }
  }
}
""")
    cfg = load_tsconfig(str(tmp_path))
    assert cfg.base_url == "./src"
    assert cfg.paths == {
        "@/util": ["util.ts"],
        "@/calc/*": ["calc/*"],
    }


def test_malformed_json_returns_empty(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{ this is not json")
    cfg = load_tsconfig(str(tmp_path))
    assert cfg.base_url is None
    assert cfg.paths == {}


def test_missing_compileroptions_is_ok(tmp_path):
    (tmp_path / "tsconfig.json").write_text('{"files": ["src/index.ts"]}')
    cfg = load_tsconfig(str(tmp_path))
    assert cfg.base_url is None
    assert cfg.paths == {}


def test_resolve_alias_substitutes_pattern():
    cfg = TsConfig(
        base_url="./src",
        paths={"@/util": ["util.ts"], "@/calc/*": ["calc/*"]},
    )
    assert cfg.resolve_alias("@/util") == "src/util.ts"
    assert cfg.resolve_alias("@/calc/sum") == "src/calc/sum"
    assert cfg.resolve_alias("nothing-matches") is None
```

- [ ] **Step 2: Run, expect collection error**

- [ ] **Step 3: Implement**

Create `livegraph/static_ts/tsconfig.py`:

```python
"""Minimal tsconfig.json reader: extracts baseUrl + paths aliases."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TsConfig:
    base_url: str | None = None
    paths: dict[str, list[str]] = field(default_factory=dict)

    def resolve_alias(self, specifier: str) -> str | None:
        """Try to match ``specifier`` against the configured paths.

        Returns the substituted path (joined with base_url) on a match,
        or None if no alias matches. Supports the trailing-``*`` wildcard
        form documented by tsconfig.
        """
        base = (self.base_url or ".").rstrip("/")
        # Try exact match first.
        if specifier in self.paths:
            target = self.paths[specifier][0]
            return _join(base, target)
        # Then wildcard matches: pattern ends in /*, specifier must start
        # with the prefix.
        for pat, targets in self.paths.items():
            if not pat.endswith("/*"):
                continue
            prefix = pat[:-2]
            if specifier.startswith(prefix + "/"):
                rest = specifier[len(prefix) + 1:]
                target = targets[0]
                if target.endswith("/*"):
                    target = target[:-2] + "/" + rest
                return _join(base, target)
        return None


def _join(base: str, target: str) -> str:
    """Join base + target, stripping a leading ``./`` from base."""
    if base in (".", "./"):
        return target.lstrip("./")
    base = base[2:] if base.startswith("./") else base
    return f"{base}/{target}".replace("//", "/")


def load_tsconfig(project_root: str) -> TsConfig:
    """Read ``<root>/tsconfig.json``. Returns empty config on missing/bad."""
    path = os.path.join(project_root, "tsconfig.json")
    if not os.path.exists(path):
        return TsConfig()
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("malformed tsconfig.json: %s", exc)
        return TsConfig()

    co = data.get("compilerOptions") or {}
    base_url = co.get("baseUrl")
    paths = co.get("paths") or {}
    if not isinstance(paths, dict):
        paths = {}
    return TsConfig(base_url=base_url, paths=paths)
```

- [ ] **Step 4: Run tests, expect 5 PASS**

- [ ] **Step 5: Commit**

```bash
git add livegraph/static_ts/tsconfig.py tests/unit/test_static_ts_tsconfig.py
git commit -m "feat(phase13): tsconfig.json reader (baseUrl + paths)"
```

---

## Task 3: Extractor

**Files:**
- Create: `livegraph/static_ts/extractor.py`
- Test: `tests/unit/test_static_ts_extractor.py`

- [ ] **Step 1: Inspect the Python extractor's interface**

Read `/Users/yvon.zhu/Documents/GitHub/livegraph/livegraph/static/extractor.py` to confirm:

- Returns `(list[Definition], list[ImportRecord], list[RawCall])`.
- `Definition` lives in `livegraph/models.py`; key fields: `qualified_name`, `kind` (`"function"|"method"|"class"`), `name`, `file`, `start_line`, `end_line`, `parent_class`, `source`.
- `ImportRecord` fields: `file`, `target`, `raw`, `line`, `target_kind` (`"file"` for `from X import Y` resolved-or-unresolved hint set by resolver).
- `RawCall` lives in the same module as the Python extractor: `caller_qn`, `callee_name`, `line`.

The TS extractor mirrors this exactly — same return tuple, same model types. Reuse `RawCall` from `livegraph.static.extractor` (avoid duplicating).

- [ ] **Step 2: Write the failing tests**

```python
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
    assert imp.target == "./calc"
    assert imp.line == 1


def test_namespace_import():
    src = b'import * as ns from "./util";\n'
    _defs, imports, _calls = extract("src/m.ts", src)
    assert any(i.target == "./util" for i in imports)


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
```

- [ ] **Step 3: Run, expect collection error**

- [ ] **Step 4: Implement the extractor**

Create `livegraph/static_ts/extractor.py`:

```python
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
        target = _import_source(node, source)
        if target is not None:
            imports.append(ImportRecord(
                file=rel_path, target=target, raw=target,
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
            if init is None or init.type not in (
                "arrow_function", "function_expression",
            ):
                continue
            ident = declarator.child_by_field_name("name")
            if ident is None or ident.type != "identifier":
                continue
            name = _text(ident, source)
            qn = symbol_qid(rel_path, name)
            definitions.append(_def(
                qn, "function", name, rel_path, node, source,
                parent_class=None,
            ))
            _walk_children(init, source, rel_path, qn, None,
                           definitions, imports, calls)
        return

    # ---- export default function (named or anonymous) --------------
    if nt == "export_statement":
        # Check the export's child for a default function-like.
        default_kw = any(
            c.type == "default" for c in node.children
        )
        for child in node.children:
            if child.type in ("function_declaration", "arrow_function",
                              "function_expression"):
                if default_kw:
                    qn = symbol_qid(rel_path, "default")
                    definitions.append(_def(
                        qn, "function", "default", rel_path, node, source,
                        parent_class=None,
                    ))
                    _walk_children(child, source, rel_path, qn, None,
                                   definitions, imports, calls)
                    return
        # If no default function-like, fall through and recurse.

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
        # Fall through to recurse into arguments.

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
        parent_class=parent_class,
        source=_text(node, source),
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
    # Strip the surrounding quotes.
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
        # new_expression: look at child[1] typically.
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
```

- [ ] **Step 5: Run tests, expect 10 PASS**

If a test fails because of tree-sitter-typescript grammar differences (e.g., the `default_kw` detection picks up the wrong child), inspect the actual tree with a one-off REPL:

```python
from livegraph.static_ts.parser import parse_source
t = parse_source(b"export default function foo() {}\n", jsx=False)
print(t.root_node.sexp())
```

Then adjust the field names. Do not loosen tests; fix the extractor.

- [ ] **Step 6: Commit**

```bash
git add livegraph/static_ts/extractor.py tests/unit/test_static_ts_extractor.py
git commit -m "feat(phase13): TS/JS extractor — definitions, imports, raw calls"
```

---

## Task 4: Resolver

**Files:**
- Create: `livegraph/static_ts/resolver.py`
- Test: `tests/unit/test_static_ts_resolver.py`

- [ ] **Step 1: Read the Python resolver for reference**

`/Users/yvon.zhu/Documents/GitHub/livegraph/livegraph/static/resolver.py` shows the shape: `resolve_imports(imports, project_modules) -> list[ResolvedImport]` and `resolve_calls(raw_calls, defined) -> list[CallEdge]`. Reuse the same `ResolvedImport` and `CallEdge` model types from `livegraph/models.py`.

- [ ] **Step 2: Write the failing tests**

```python
from __future__ import annotations

from livegraph.models import ImportRecord
from livegraph.static.extractor import RawCall
from livegraph.static_ts.resolver import resolve_calls, resolve_imports
from livegraph.static_ts.tsconfig import TsConfig


def test_relative_import_resolves_with_extension_inference():
    imports = [ImportRecord(
        file="src/index.ts", target="./calc", raw="./calc", line=1,
    )]
    project_files = {"src/calc.ts", "src/index.ts"}
    resolved = resolve_imports(
        imports, project_files=project_files, tsconfig=TsConfig(),
    )
    assert len(resolved) == 1
    r = resolved[0]
    assert r.file == "src/index.ts"
    assert r.target == "src/calc.ts"
    assert r.target_kind == "file"


def test_relative_import_resolves_to_index_when_directory():
    imports = [ImportRecord(
        file="src/index.ts", target="./store", raw="./store", line=1,
    )]
    project_files = {"src/store/index.ts", "src/index.ts"}
    resolved = resolve_imports(
        imports, project_files=project_files, tsconfig=TsConfig(),
    )
    assert resolved[0].target == "src/store/index.ts"


def test_tsconfig_alias_resolves():
    imports = [ImportRecord(
        file="src/index.ts", target="@/util", raw="@/util", line=1,
    )]
    project_files = {"src/util.ts", "src/index.ts"}
    cfg = TsConfig(base_url="./src", paths={"@/util": ["util.ts"]})
    resolved = resolve_imports(
        imports, project_files=project_files, tsconfig=cfg,
    )
    assert resolved[0].target == "src/util.ts"
    assert resolved[0].target_kind == "file"


def test_bare_module_unresolved_kind_module():
    imports = [ImportRecord(
        file="src/index.ts", target="react", raw="react", line=1,
    )]
    resolved = resolve_imports(
        imports, project_files=set(), tsconfig=TsConfig(),
    )
    assert resolved[0].target == "react"
    assert resolved[0].target_kind != "file"  # external module


def test_resolve_calls_matches_defined_symbols():
    raw = [
        RawCall(caller_qn="src/a.ts::f", callee_name="bar", line=1),
        RawCall(caller_qn="src/a.ts::f", callee_name="missing", line=2),
    ]
    defined = {"src/a.ts::f", "src/b.ts::bar"}
    edges = resolve_calls(raw, defined)
    callees = {e.callee_qn for e in edges}
    assert "src/b.ts::bar" in callees
    # 'missing' has no defined symbol — no edge.
```

- [ ] **Step 3: Run, expect collection error**

- [ ] **Step 4: Implement the resolver**

Create `livegraph/static_ts/resolver.py`:

```python
"""Resolve TS imports and calls against the project's defined symbols."""
from __future__ import annotations

import os
from collections.abc import Iterable

from livegraph.models import CallEdge, ImportRecord
from livegraph.static.extractor import RawCall
from livegraph.static.resolver import ResolvedImport
from livegraph.static_ts.tsconfig import TsConfig

_TS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def resolve_imports(
    imports: Iterable[ImportRecord],
    *,
    project_files: set[str],
    tsconfig: TsConfig,
) -> list[ResolvedImport]:
    """Resolve TS import specifiers to project files or external modules."""
    out: list[ResolvedImport] = []
    for imp in imports:
        spec = imp.target
        resolved = _resolve_specifier(
            spec, importing_file=imp.file,
            project_files=project_files, tsconfig=tsconfig,
        )
        if resolved is not None:
            out.append(ResolvedImport(
                file=imp.file, target=resolved, raw=imp.raw,
                line=imp.line, target_kind="file",
            ))
        else:
            # Bare specifier or alias that doesn't match a project file:
            # record as external module.
            out.append(ResolvedImport(
                file=imp.file, target=spec, raw=imp.raw,
                line=imp.line, target_kind="module",
            ))
    return out


def _resolve_specifier(
    spec: str, *, importing_file: str,
    project_files: set[str], tsconfig: TsConfig,
) -> str | None:
    """Return the project-relative file path the specifier resolves to."""
    if spec.startswith("./") or spec.startswith("../"):
        importer_dir = os.path.dirname(importing_file)
        candidate = os.path.normpath(os.path.join(importer_dir, spec))
        candidate = candidate.replace("\\", "/")
        return _expand_to_existing_file(candidate, project_files)
    # tsconfig alias?
    aliased = tsconfig.resolve_alias(spec)
    if aliased is not None:
        return _expand_to_existing_file(aliased, project_files)
    return None


def _expand_to_existing_file(
    candidate: str, project_files: set[str],
) -> str | None:
    """Try the literal path, then ext suffixes, then index files."""
    if candidate in project_files:
        return candidate
    for ext in _TS_EXTS:
        if (candidate + ext) in project_files:
            return candidate + ext
    for ext in _TS_EXTS:
        idx = f"{candidate}/index{ext}"
        if idx in project_files:
            return idx
    return None


def resolve_calls(
    raw_calls: Iterable[RawCall], defined: set[str],
) -> list[CallEdge]:
    """Match raw call sites by simple-name against the project's symbols.

    Mirrors the Python resolver: a callee_name matches a defined qname
    whose last dotted segment equals it. Multiple matches produce
    multiple edges (over-generation is intentional; agents narrow with
    `provenance` filters elsewhere).
    """
    # Build a name -> [qns] index for fast lookup.
    by_name: dict[str, list[str]] = {}
    for qn in defined:
        last = qn.rsplit(".", 1)[-1]
        # Also handle the file:: prefix for module-level names.
        if "::" in last:
            last = last.split("::", 1)[-1]
        by_name.setdefault(last, []).append(qn)

    edges: list[CallEdge] = []
    for call in raw_calls:
        candidates = by_name.get(call.callee_name) or []
        for callee_qn in candidates:
            edges.append(CallEdge(
                caller_qn=call.caller_qn, callee_qn=callee_qn,
                line=call.line, static=True, runtime=False,
            ))
    return edges
```

If the existing `CallEdge` model doesn't have `static`/`runtime` boolean fields, inspect `livegraph/models.py` and use whatever shape the Python pipeline produces. The Python `resolve_calls` returns `CallEdge` instances — match that shape exactly.

- [ ] **Step 5: Run tests, expect 5 PASS**

- [ ] **Step 6: Commit**

```bash
git add livegraph/static_ts/resolver.py tests/unit/test_static_ts_resolver.py
git commit -m "feat(phase13): TS resolver — imports (relative/alias/bare) + calls"
```

---

## Task 5: Discovery widened to TS

**Files:**
- Modify: `livegraph/discovery.py`
- Test: `tests/unit/test_discovery_ts.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path

from livegraph.discovery import discover_typescript_files


def test_finds_ts_tsx_js_jsx_mjs_cjs(tmp_path: Path):
    for name in ["a.ts", "b.tsx", "c.js", "d.jsx", "e.mjs", "f.cjs"]:
        (tmp_path / name).write_text("// x\n")
    out = sorted(discover_typescript_files(str(tmp_path)))
    assert out == ["a.ts", "b.tsx", "c.js", "d.jsx", "e.mjs", "f.cjs"]


def test_skips_node_modules_and_dist(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("// x\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.ts").write_text("// x\n")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "out.js").write_text("// x\n")
    out = sorted(discover_typescript_files(str(tmp_path)))
    assert out == ["src/a.ts"]


def test_ignores_non_ts_files(tmp_path: Path):
    (tmp_path / "a.ts").write_text("// x\n")
    (tmp_path / "README.md").write_text("hi\n")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    out = sorted(discover_typescript_files(str(tmp_path)))
    assert out == ["a.ts"]
```

- [ ] **Step 2: Implement**

In `/Users/yvon.zhu/Documents/GitHub/livegraph/livegraph/discovery.py`, after `discover_python_files`, add:

```python
_TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def discover_typescript_files(root: str) -> Iterator[str]:
    """Yield project-relative, forward-slash paths of every TS/JS file."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(_TS_EXTENSIONS):
                abs_path = os.path.join(dirpath, filename)
                yield os.path.relpath(abs_path, root).replace("\\", "/")
```

`_SKIP_DIRS` already includes `node_modules`, `dist`, etc. for Python — confirm by reading the existing file. If `dist` isn't there, add it (it's TS-conventional and shouldn't affect Python ingest since Python projects rarely have a top-level `dist/`).

- [ ] **Step 3: Run tests, expect 3 PASS**

- [ ] **Step 4: Commit**

```bash
git add livegraph/discovery.py tests/unit/test_discovery_ts.py
git commit -m "feat(phase13): discover_typescript_files"
```

---

## Task 6: Ingest dispatch

**Files:**
- Modify: `livegraph/ingest.py`
- Test: `tests/unit/test_ingest_ts_dispatch.py`

- [ ] **Step 1: Inspect the current `ingest_project`**

Read `/Users/yvon.zhu/Documents/GitHub/livegraph/livegraph/ingest.py:34-83`. It currently:
1. Discovers `.py` files.
2. Builds the project-modules map (Python-specific).
3. For each file: open, hash, parse, extract.
4. Resolves calls + imports.
5. Writes files + defs + imports + calls.

The dispatch refactor: add an optional `lang` parameter (`"auto"` / `"python"` / `"typescript"`); when `lang` is `"auto"` or `"typescript"`, also discover + extract TS files; merge `all_defs`/`all_imports`/`all_raw_calls` from both pipelines before resolving.

A subtle bit: `resolve_imports` in the Python pipeline takes `project_modules` (dotted → file); the TS resolver takes `project_files` (set of file paths) + `tsconfig`. They need separate calls. So the orchestrator collects each pipeline's imports separately, resolves each, then concatenates the resolved-import lists before writing.

- [ ] **Step 2: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from livegraph.ingest import ingest_project


class _RecordingBackend:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any):
        self.calls.append((cypher, params))
        return []

    def verify(self): return None
    def close(self): return None


def _make_mixed_project(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "calc.py").write_text(
        "def py_add(a, b): return a + b\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "util.ts").write_text(
        "export function tsNormalize(x: string): string { return x.trim(); }\n"
    )
    return tmp_path


def test_auto_dispatch_runs_both_pipelines(tmp_path: Path):
    project = _make_mixed_project(tmp_path)
    backend = _RecordingBackend()
    summary = ingest_project(str(project), backend, project_name="mixed")
    # Files from both languages.
    file_calls = [c for c in backend.calls if "MERGE (f:File" in c[0]]
    paths = set()
    for c in file_calls:
        rows = c[1].get("rows") or []
        for row in rows:
            paths.add(row.get("path"))
    assert "pkg/calc.py" in paths
    assert "src/util.ts" in paths
    assert summary.files >= 3


def test_lang_python_skips_ts(tmp_path: Path):
    project = _make_mixed_project(tmp_path)
    backend = _RecordingBackend()
    summary = ingest_project(
        str(project), backend, project_name="mixed", lang="python",
    )
    file_calls = [c for c in backend.calls if "MERGE (f:File" in c[0]]
    paths = set()
    for c in file_calls:
        for row in (c[1].get("rows") or []):
            paths.add(row.get("path"))
    assert "pkg/calc.py" in paths
    assert "src/util.ts" not in paths


def test_lang_typescript_skips_python(tmp_path: Path):
    project = _make_mixed_project(tmp_path)
    backend = _RecordingBackend()
    summary = ingest_project(
        str(project), backend, project_name="mixed", lang="typescript",
    )
    file_calls = [c for c in backend.calls if "MERGE (f:File" in c[0]]
    paths = set()
    for c in file_calls:
        for row in (c[1].get("rows") or []):
            paths.add(row.get("path"))
    assert "src/util.ts" in paths
    assert "pkg/calc.py" not in paths
```

- [ ] **Step 3: Run, expect failures (lang parameter doesn't exist)**

- [ ] **Step 4: Refactor `ingest_project`**

In `livegraph/ingest.py`, at the top with the existing imports, add:

```python
from livegraph.discovery import discover_typescript_files
from livegraph.static_ts.extractor import extract as ts_extract
from livegraph.static_ts.parser import (
    has_errors as ts_has_errors, is_jsx_file, parse_source as ts_parse_source,
)
from livegraph.static_ts.resolver import (
    resolve_calls as ts_resolve_calls,
    resolve_imports as ts_resolve_imports,
)
from livegraph.static_ts.tsconfig import load_tsconfig
```

Modify the `ingest_project` signature:

```python
def ingest_project(
    root: str, backend: GraphBackend, project_name: str | None = None,
    batch_size: int = 1000, lang: str = "auto",
) -> IngestSummary:
```

Replace the body with a dispatch that runs each language's pipeline:

```python
    project_name = project_name or os.path.basename(os.path.abspath(root))
    backend.verify()
    create_schema(backend)
    writer = GraphWriter(backend, batch_size=batch_size)

    do_py = lang in ("auto", "python")
    do_ts = lang in ("auto", "typescript")

    py_rels = sorted(discover_python_files(root)) if do_py else []
    ts_rels = sorted(discover_typescript_files(root)) if do_ts else []

    file_records: list[FileRecord] = []
    all_defs = []
    all_raw_calls = []
    py_imports = []
    ts_imports = []
    parse_errors = 0

    # Python files.
    project_modules = {module_name(p): p for p in py_rels}
    for rel in py_rels:
        with open(os.path.join(root, rel), "rb") as handle:
            source = handle.read()
        content_hash = hashlib.sha256(source).hexdigest()
        broken = has_errors(parse_source(source))
        file_records.append(FileRecord(
            path=rel, name=os.path.basename(rel), parse_error=broken,
            content_hash=content_hash))
        if broken:
            parse_errors += 1
            logger.warning("skipping unparseable file: %s", rel)
            continue
        defs, imps, raw_calls = extract(rel, source)
        all_defs.extend(defs)
        py_imports.extend(imps)
        all_raw_calls.extend(raw_calls)

    # TypeScript files.
    tsconfig = load_tsconfig(root) if do_ts else None
    project_files_set = set(ts_rels)
    for rel in ts_rels:
        with open(os.path.join(root, rel), "rb") as handle:
            source = handle.read()
        content_hash = hashlib.sha256(source).hexdigest()
        broken = ts_has_errors(ts_parse_source(source, jsx=is_jsx_file(rel)))
        file_records.append(FileRecord(
            path=rel, name=os.path.basename(rel), parse_error=broken,
            content_hash=content_hash))
        if broken:
            parse_errors += 1
            logger.warning("skipping unparseable file: %s", rel)
            continue
        defs, imps, raw_calls = ts_extract(rel, source)
        all_defs.extend(defs)
        ts_imports.extend(imps)
        all_raw_calls.extend(raw_calls)

    defined = {d.qualified_name for d in all_defs}
    call_edges = resolve_calls(all_raw_calls, defined)
    # TS resolver does the same name-match — but to avoid double-resolving
    # Python calls through TS rules, we only feed it the raw TS calls.
    # Actually: Python's resolve_calls returns ALL edges across the unified
    # `defined` set already; TS's resolve_calls would duplicate. Don't call
    # ts_resolve_calls separately — let Python's resolver handle the merged
    # name-match for both. See the Python resolver in static/resolver.py.
    py_resolved_imports = resolve_imports(py_imports, project_modules)
    ts_resolved_imports = ts_resolve_imports(
        ts_imports, project_files=project_files_set, tsconfig=tsconfig or load_tsconfig(root),
    )
    resolved_imports_all = py_resolved_imports + ts_resolved_imports

    writer.write_files(project_name, file_records,
                       root_path=os.path.abspath(root))
    writer.write_definitions(all_defs)
    _write_imports(backend, resolved_imports_all, batch_size)
    writer.write_calls(call_edges)

    return IngestSummary(
        files=len(file_records), definitions=len(all_defs),
        call_edges=len(call_edges), parse_errors=parse_errors,
    )
```

A subtlety to verify: the existing Python `resolve_calls` does name-matching only against names that look Python-y (it doesn't care about the qname format — it splits on `.`). If TS qnames carry `file_path::Class.method`, the rightmost `.` segment is the method name — should work for `Class.method` style qnames. For top-level functions (`src/m.ts::foo`), `rsplit(".", 1)` returns `["src/m.ts::foo"]` so the "name" is the full qname — that's wrong. The TS resolver implemented in Task 4 handles this with `split("::", 1)`. The Python resolver doesn't.

So we actually do need both: Python's `resolve_calls` matches against Python-defined qnames; TS's `resolve_calls` matches against TS-defined qnames. To avoid double-edges on cross-language collisions (`pkg.foo` and `src/x.ts::foo`), feed each resolver only its own side's `defined` set. Update accordingly:

```python
    py_defined = {d.qualified_name for d in all_defs
                  if "::" not in d.qualified_name.split(".", 1)[0]
                  or d.file.endswith(".py")}
    # Simpler: filter by file extension on the Definition record.
    py_defined = {d.qualified_name for d in all_defs if d.file.endswith(".py")}
    ts_defined = {d.qualified_name for d in all_defs
                  if not d.file.endswith(".py")}
    py_raw = [r for r in all_raw_calls if "::" not in r.caller_qn or r.caller_qn.split("::")[0].endswith(".py")]
    ts_raw = [r for r in all_raw_calls
              if not (("::" not in r.caller_qn) or r.caller_qn.split("::")[0].endswith(".py"))]
    call_edges = resolve_calls(py_raw, py_defined) + ts_resolve_calls(ts_raw, ts_defined)
```

(That filter logic is brittle. Simpler still: tag `RawCall` with the language at extraction time. But adding a field to a shared dataclass is intrusive. Just use the `caller_qn`'s file-prefix as the signal: if the prefix path ends in `.py`, it's Python.)

- [ ] **Step 5: Run tests, expect 3 PASS**

```
.venv/bin/python -m pytest tests/unit/test_ingest_ts_dispatch.py -v
```

Then full unit suite:

```
.venv/bin/python -m pytest tests/unit/ -q
```

Existing Python ingest tests must still pass.

- [ ] **Step 6: Commit**

```bash
git add livegraph/ingest.py tests/unit/test_ingest_ts_dispatch.py
git commit -m "feat(phase13): ingest_project dispatches to both Python and TS pipelines"
```

---

## Task 7: Incremental + watch integration

**Files:**
- Modify: `livegraph/incremental.py`
- Modify: `livegraph/watch/filters.py`
- Modify: `tests/unit/test_watch_filters.py` (extend)

- [ ] **Step 1: Inspect `livegraph/incremental.py`**

Find `detect_changes` and `reingest_files`. The former walks the project; the latter handles per-file extract/resolve. Both currently assume `.py`.

- [ ] **Step 2: Modify `detect_changes`**

In `livegraph/incremental.py`, the function `detect_changes` calls `discover_python_files`. Replace that with a combined walk:

```python
on_disk: dict[str, str] = {}
for rel in discover_python_files(root):
    abs_path = os.path.join(root, rel)
    with open(abs_path, "rb") as handle:
        on_disk[rel] = hashlib.sha256(handle.read()).hexdigest()
for rel in discover_typescript_files(root):
    abs_path = os.path.join(root, rel)
    with open(abs_path, "rb") as handle:
        on_disk[rel] = hashlib.sha256(handle.read()).hexdigest()
```

Add `from livegraph.discovery import discover_typescript_files` near the top.

- [ ] **Step 3: Modify `reingest_files`**

Find the per-file extract path inside `reingest_files`. Currently it calls `extract(rel, source)` from `livegraph.static.extractor` and `parse_source` / `has_errors` from `livegraph.static.parser`. Wrap dispatch by extension:

```python
from livegraph.static_ts.extractor import extract as ts_extract
from livegraph.static_ts.parser import (
    has_errors as ts_has_errors, is_jsx_file, parse_source as ts_parse_source,
)

def _extract_for(rel: str, source: bytes):
    if rel.endswith(".py"):
        from livegraph.static.parser import parse_source, has_errors
        from livegraph.static.extractor import extract
        if has_errors(parse_source(source)):
            return None
        return extract(rel, source)
    if rel.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        if ts_has_errors(ts_parse_source(source, jsx=is_jsx_file(rel))):
            return None
        return ts_extract(rel, source)
    return None
```

Use `_extract_for(rel, source)` at the existing extract call site. `None` means parse error — record the file with `parse_error=True` and skip definitions.

The call-resolution Phase B of `reingest_files` also needs both `project_defined` (already returns Function+Method+Class qnames regardless of language) and per-language import resolution. For incremental TS, run `ts_resolve_imports` with the TS-files set discovered from the project. Add the same per-language split as Task 6.

This is the most intricate change — read the existing `reingest_files` carefully (~80 lines) and weave in the TS branch. Test by re-running the existing Phase 5 tests AFTER making your changes to ensure no regression.

- [ ] **Step 4: Run existing Phase 5 tests, expect all PASS**

```
.venv/bin/python -m pytest tests/unit/test_incremental.py tests/unit/test_incremental_update_files.py -v
```

If anything fails, look at the failing test's setup — the Python ingest path must remain unchanged for inputs that only have `.py` files.

- [ ] **Step 5: Modify `watch/filters.py`**

Find the `PathFilter.accepts` method. Currently it accepts `.py` only:

```python
if path.suffix != ".py":
    return False
```

Replace with:

```python
if path.suffix not in (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
    return False
```

- [ ] **Step 6: Extend `tests/unit/test_watch_filters.py`**

Add new tests at the bottom (don't modify existing ones):

```python
def test_typescript_files_accepted(tmp_path):
    pf = make_filter(tmp_path)
    for name in ["a.ts", "b.tsx", "c.js", "d.jsx", "e.mjs", "f.cjs"]:
        assert pf.accepts(tmp_path / name) is True


def test_other_extensions_rejected(tmp_path):
    pf = make_filter(tmp_path)
    assert pf.accepts(tmp_path / "x.go") is False
    assert pf.accepts(tmp_path / "x.rs") is False
```

(`make_filter` is the helper already in the file.)

- [ ] **Step 7: Run tests + full unit suite**

```
.venv/bin/python -m pytest tests/unit/test_watch_filters.py tests/unit/test_incremental.py -v
.venv/bin/python -m pytest tests/unit/ -q
```

- [ ] **Step 8: Commit**

```bash
git add livegraph/incremental.py livegraph/watch/filters.py tests/unit/test_watch_filters.py
git commit -m "feat(phase13): incremental update + watch picks up TS files"
```

---

## Task 8: CLI `--lang` flag

**Files:**
- Modify: `livegraph/cli.py`
- Modify: `tests/unit/test_cli.py` (extend)

- [ ] **Step 1: Add `--lang` to `build` and `ingest` commands**

In `livegraph/cli.py`, the `build` command currently has signature:

```python
def build(
    path: str = typer.Argument(...),
    python: str = typer.Option(None, "--python", help="Target interpreter"),
) -> None:
```

Add a `lang` option after `python`:

```python
    lang: str = typer.Option(
        "auto", "--lang",
        help="Which language(s) to ingest: auto, python, typescript",
    ),
```

And pass it through to `ingest_project(... lang=lang)`. Same for the `ingest` command.

The Python tracer (`run_pytest` / `augment_from_observations`) is Python-specific — it should only run when `lang in ("auto", "python")`. Add the guard:

```python
        if lang in ("auto", "python"):
            try:
                observations = run_pytest(path, python=python)
                ...
            except RuntimeUnavailable as exc:
                typer.echo(f"Phase 2 skipped: {exc}", err=True)
```

Validate `lang` upfront:

```python
    if lang not in ("auto", "python", "typescript"):
        typer.echo(f"unknown --lang: {lang!r}", err=True)
        raise typer.Exit(code=2)
```

- [ ] **Step 2: Add CLI tests**

In `tests/unit/test_cli.py`, add:

```python
def test_build_help_mentions_lang(monkeypatch):
    result = runner.invoke(app, ["build", "--help"])
    assert result.exit_code == 0
    assert "--lang" in result.stdout


def test_build_invalid_lang_exits_2(monkeypatch, tmp_path):
    result = runner.invoke(app, ["build", str(tmp_path), "--lang", "rust"])
    assert result.exit_code == 2
```

(`runner` and `app` already exist in that file.)

- [ ] **Step 3: Run tests**

```
.venv/bin/python -m pytest tests/unit/test_cli.py -v
```

- [ ] **Step 4: Commit**

```bash
git add livegraph/cli.py tests/unit/test_cli.py
git commit -m "feat(phase13): --lang flag on build/ingest (auto|python|typescript)"
```

---

## Task 9: Sample TS project fixture

**Files:**
- Create: `tests/fixtures/sample_project_ts/package.json`
- Create: `tests/fixtures/sample_project_ts/tsconfig.json`
- Create: `tests/fixtures/sample_project_ts/src/calc.ts`
- Create: `tests/fixtures/sample_project_ts/src/util.ts`
- Create: `tests/fixtures/sample_project_ts/src/index.ts`
- Create: `tests/fixtures/sample_project_ts/tests/calc.test.ts`

- [ ] **Step 1: Create the fixture files**

`tests/fixtures/sample_project_ts/package.json`:
```json
{ "name": "sample-ts", "version": "0.0.0" }
```

`tests/fixtures/sample_project_ts/tsconfig.json`:
```json
{
  "compilerOptions": {
    "baseUrl": "./src",
    "paths": {
      "@/util": ["util.ts"]
    }
  }
}
```

`tests/fixtures/sample_project_ts/src/calc.ts`:
```typescript
export class Calculator {
  add(a: number, b: number): number {
    return a + b;
  }
  multiply(a: number, b: number): number {
    return a * b;
  }
}
```

`tests/fixtures/sample_project_ts/src/util.ts`:
```typescript
export function normalize(s: string): string {
  return s.trim().toLowerCase();
}
```

`tests/fixtures/sample_project_ts/src/index.ts`:
```typescript
import { Calculator } from "./calc";
import { normalize } from "@/util";

export default function main() {
  const c = new Calculator();
  const sum = c.add(1, 2);
  return normalize(`sum=${sum}`);
}
```

`tests/fixtures/sample_project_ts/tests/calc.test.ts`:
```typescript
import { Calculator } from "../src/calc";

function test_add() {
  const c = new Calculator();
  return c.add(2, 3);
}
```

- [ ] **Step 2: Smoke-test by parsing each file**

```
.venv/bin/python -c "
from livegraph.static_ts.parser import parse_source, has_errors, is_jsx_file
import pathlib
root = pathlib.Path('tests/fixtures/sample_project_ts')
for p in root.rglob('*'):
    if p.suffix in ('.ts', '.tsx', '.js', '.jsx'):
        src = p.read_bytes()
        t = parse_source(src, jsx=is_jsx_file(str(p)))
        print(p, 'OK' if not has_errors(t) else 'PARSE-ERR')
"
```

All files should print `OK`.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/sample_project_ts
git commit -m "test(phase13): sample TS project fixture"
```

---

## Task 10: Integration test

**Files:**
- Create: `tests/integration/test_typescript_integration.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end: real Neo4j + the sample TS project."""
from __future__ import annotations

import os

import pytest

from livegraph.ingest import ingest_project

pytestmark = pytest.mark.integration

SAMPLE_TS = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "sample_project_ts",
)


@pytest.fixture()
def ts_project(neo4j_backend):
    root = os.path.abspath(SAMPLE_TS)
    summary = ingest_project(root, neo4j_backend, project_name="sample_ts")
    assert summary.files >= 4
    return neo4j_backend, "sample_ts"


def test_calculator_class_and_methods_ingested(ts_project):
    backend, project = ts_project
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->"
        "(:File {path: 'src/calc.ts'})-[:DEFINES]->(c:Class) "
        "RETURN c.qualified_name AS qn",
        project=project,
    )
    qns = {r["qn"] for r in rows}
    assert "src/calc.ts::Calculator" in qns


def test_methods_attached_to_class(ts_project):
    backend, project = ts_project
    rows = backend.execute(
        "MATCH (:Class {qualified_name: 'src/calc.ts::Calculator'})"
        "-[:HAS_METHOD]->(m:Method) "
        "RETURN m.qualified_name AS qn",
    )
    qns = {r["qn"] for r in rows}
    assert "src/calc.ts::Calculator.add" in qns
    assert "src/calc.ts::Calculator.multiply" in qns


def test_tsconfig_alias_resolves_to_file_import(ts_project):
    backend, project = ts_project
    rows = backend.execute(
        "MATCH (:File {path: 'src/index.ts'})-[:IMPORTS]->(f:File) "
        "RETURN f.path AS path",
    )
    paths = {r["path"] for r in rows}
    assert "src/util.ts" in paths  # via @/util alias
    assert "src/calc.ts" in paths  # via relative ./calc


def test_index_calls_calculator_add(ts_project):
    backend, project = ts_project
    rows = backend.execute(
        "MATCH (caller {qualified_name: 'src/index.ts::default'})"
        "-[:CALLS]->(callee) "
        "WHERE callee.qualified_name = 'src/calc.ts::Calculator.add' "
        "RETURN count(*) AS n",
    )
    assert rows[0]["n"] >= 1


def test_default_export_named_default(ts_project):
    backend, project = ts_project
    rows = backend.execute(
        "MATCH (:File {path: 'src/index.ts'})-[:DEFINES]->(f) "
        "WHERE f.qualified_name = 'src/index.ts::default' "
        "RETURN f.qualified_name AS qn",
    )
    assert rows[0]["qn"] == "src/index.ts::default"
```

- [ ] **Step 2: Run with Neo4j up**

```
.venv/bin/python -m pytest tests/integration/test_typescript_integration.py -v -m integration
```
Expected: 5 PASS (or skip if Neo4j unreachable).

If a test fails for a real reason (not skip), pause and report:
- If `Calculator` doesn't appear: extractor isn't capturing `export class`. Inspect the tree.
- If the alias-import test fails: tsconfig resolver path-join is wrong.
- If the call to `Calculator.add` doesn't fire: `_call_target_name` may not be extracting `add` from `c.add(1, 2)`.

- [ ] **Step 3: Run the full suite for regressions**

```
.venv/bin/python -m pytest -q
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_typescript_integration.py
git commit -m "test(phase13): TypeScript ingest end-to-end against real Neo4j"
```

---

## Task 11: README section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a section to the end of `README.md`**

```markdown

## TypeScript / JavaScript support

Phase 13 makes livegraph multi-language. `livegraph build /path/to/repo`
auto-detects what's there:

- `*.py` → Python pipeline (Phases 1-2).
- `*.ts`, `*.tsx`, `*.js`, `*.jsx`, `*.mjs`, `*.cjs` → TypeScript pipeline.

Both populate the same Neo4j schema, so an agent can ask
`find_symbol`, `find_callers`, `find_cycles`, `semantic_search` over a
mixed Python + TS monorepo and get unified results.

What gets captured for TS:

- **Definitions**: `function`, `const x = () => {}`, `class C { method() {} }`,
  `export function`, `export default function` (recorded as `default`).
- **Imports**: ES modules — relative paths (with extension inference
  and `index.{ts,tsx,…}` resolution), tsconfig `paths` aliases (read
  from `tsconfig.json` if present), bare specifiers (recorded as
  external `:Module` nodes).
- **Calls**: direct calls (`foo()`), method calls (`obj.m()`), `new C()`.
  Name-matched against project-defined symbols, like Python.

Qualified names follow the existing `<file_path>::<dotted>` scheme,
e.g. `src/calc.ts::Calculator.add`.

**Out of scope for v1**: CommonJS `require()`, dynamic `import()`,
JSX components as call edges, decorators, type-aware method
resolution, Node runtime tracing (TS CALLS edges are always
`static=true, runtime=false`).

Override auto-detection with `--lang python` or `--lang typescript`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(phase13): README section for TypeScript / JavaScript support"
```

---

## Acceptance gate (manual, before PR)

- [ ] `.venv/bin/python -m pytest -q` → all tests pass.
- [ ] `.venv/bin/python -m ruff check .` → no new errors compared to main.
- [ ] Manual smoke: `livegraph build tests/fixtures/sample_project_ts --project ts_smoke` against a clean Neo4j; then `livegraph mcp --project ts_smoke` and ask an agent "what does `src/index.ts::default` call?" — should include `Calculator.add`.
- [ ] Manual smoke (mixed): clone a small mixed-language repo (or use livegraph + a TS demo subdir), run `livegraph build`, confirm both `:py` and `.ts` files appear in `graph_status`.
