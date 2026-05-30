# livegraph Phase 13 — TypeScript / JavaScript support (design)

**Date:** 2026-05-30
**Status:** Approved

## Goal

Make livegraph multi-language. Add a parallel `livegraph/static_ts/`
pipeline that parses `.ts`/`.tsx`/`.js`/`.jsx`/`.mjs`/`.cjs` files
with `tree-sitter-typescript`, extracts the same definitions / imports
/ calls shape as the existing Python pipeline, and writes into the
same Neo4j schema. `livegraph build` auto-detects which pipelines to
run; everything downstream (writer, MCP tools, `update`, `watch`, CI
`check`, Phase 7 embeddings, Phase 10 history) works without changes.

## Non-goals

- TypeScript runtime tracing (Python's Phase 2 sys.monitoring approach
  has no Node equivalent we want to take on). All TS CALLS edges get
  `static=true, runtime=false`. A future phase can add Node tracing.
- Type-aware method resolution. We use the same name-based heuristic
  as Python — `foo.bar()` matches any defined symbol named `bar`.
- CommonJS (`require("./m")`), dynamic `import("./m")`, JSX component
  call edges, decorators. All MVP-out; flagged in "out of scope".
- Cross-language CALLS edges (would require type inference / FFI
  knowledge livegraph doesn't have).
- A new `LanguageBackend` Protocol or refactor of the Python pipeline.
  We keep two parallel modules under one shared schema — speculative
  abstraction is YAGNI until a third language arrives.

## Architecture

### Per-language modules, shared schema

```
livegraph/
  static/          # Python (existing, unchanged)
    parser.py
    extractor.py
    resolver.py
  static_ts/       # TypeScript (new)
    parser.py
    extractor.py
    resolver.py
    qualnames.py
    tsconfig.py
  ingest.py        # modified: auto-detect, dispatch to both pipelines
  incremental.py   # modified: same dispatch for `update`
  watch/           # modified: filter widened to include TS extensions
```

The graph schema does not change. New `:Function` / `:Method` /
`:Class` / `:File` nodes representing TS symbols slot in alongside
Python ones. `:Symbol` (Phase 7 secondary label) is applied to TS
Function/Method too so embeddings + semantic_search work uniformly.

### Auto-detect dispatch

`livegraph build /path` walks the project once and groups files by
extension:

- `*.py` → Python pipeline (`livegraph/static/`).
- `*.ts`, `*.tsx`, `*.js`, `*.jsx`, `*.mjs`, `*.cjs` → TS pipeline
  (`livegraph/static_ts/`).

Both pipelines run; both write into the same project. Empty groups
short-circuit. A `--lang python` / `--lang typescript` opt-in flag
forces single-language mode (mostly for testing).

## Parser

`tree-sitter-typescript` exposes two grammars:

- `typescript` — handles `.ts`, `.js`, `.mjs`, `.cjs` (JS is a subset
  of TS in tree-sitter's grammar; works in practice).
- `tsx` — handles `.tsx` and `.jsx` (JSX syntax needs the TSX grammar
  even for `.jsx`).

Pick grammar by extension. Mirror Python parser shape:

```python
# livegraph/static_ts/parser.py
def parse_source(source: bytes, *, jsx: bool) -> Tree
def has_errors(tree: Tree) -> bool
```

## Definitions extracted

| Syntax | Extracted as |
|---|---|
| `function foo() {}` | `:Function {name: "foo"}` |
| `const foo = () => {}` | `:Function {name: "foo"}` |
| `const foo = function() {}` | `:Function {name: "foo"}` |
| `export function foo() {}` | `:Function {name: "foo"}` |
| `export default function foo() {}` | `:Function {name: "default"}` (also tagged with `export_name: "foo"` for grep-ability) |
| `export default function() {}` | `:Function {name: "default"}` |
| `export default <expression>` | `:Function {name: "default"}` (only when the expression is a function/arrow) |
| `class C { method() {} }` | `:Class {name: "C"}` + `:Method {name: "method"}` linked via `HAS_METHOD` |
| `class C { static m() {} }` | `:Method {name: "m", static: true}` |
| `class C { constructor() {} }` | `:Method {name: "constructor"}` |

Out of MVP: getter/setter (`get x() {}`), private fields (`#foo`),
abstract methods, type-only declarations (`interface`, `type`),
namespace blocks.

## Qualified-name scheme

`<rel_file_path>::<dotted.path>`. Examples:

- `src/calc.ts::Calculator.add`
- `src/util.ts::normalize`
- `src/api.ts::default`
- `src/store/index.ts::createStore`

This makes TS qnames visually distinct from Python's bare-dotted ones
(`myproj.calc.Calculator.add`) and survives default exports / module-
level functions cleanly. Computed at extraction time in
`livegraph/static_ts/qualnames.py`.

The existing `change_impact` MCP tool already accepts qnames as
opaque strings; it does not need changes. `semantic_search` /
`semantic_neighborhood` continue to work because they key on `:Symbol`
membership, not qname format.

## Imports

MVP supports ES modules only. Three forms:

```ts
import x from "./m"            // default import
import { a, b } from "./m"     // named imports
import * as ns from "./m"      // namespace import
```

Resolution:

1. **Relative paths** (`./m`, `../m`, `./m.ts`): resolve against the
   importing file's directory; try the literal path, then append
   `.ts`/`.tsx`/`.js`/`.jsx`/`.mjs`/`.cjs`, then `index.{ts,tsx,…}`
   for directories. First match wins.
2. **tsconfig path aliases**: read `tsconfig.json` from the project
   root once at ingest start; honor the `compilerOptions.paths` and
   `compilerOptions.baseUrl` entries. Aliases like `"@/foo"` resolve
   against `baseUrl`. If no `tsconfig.json`, this stage is a no-op.
3. **Bare module specifiers** (`react`, `lodash`, `@scope/pkg`):
   stored as `:Module {name: "react", kind: "external"}` — same shape
   the Python pipeline uses for stdlib/site-packages imports.

Writes go through the existing `GraphWriter` — same `IMPORTS` edge
type, same row shape.

## Calls

Direct call sites only:

- `foo()` — name lookup against file-local symbols, then project-wide
  defined-symbols (mirrors Python).
- `obj.method()` — name match against any `:Method` with `name:
  "method"` in the project. Like Python, fuzzy and over-generates;
  agents that need precision use `find_callers` with `provenance:
  "runtime"` — except for TS that's always empty in this phase.
- `Class.staticMethod()` — same as above, treated as a `.method()`
  call on the class name.
- `new Class()` — generates a CALLS edge to `Class`'s constructor if
  one is defined; otherwise to the class itself.

Out of MVP: tagged template literals (`` html`<div/>` ``), method
chains beyond one hop, `super()`, optional-chained calls (`a?.b()`).
The MVP captures the `?.` form too because tree-sitter exposes it
the same way as `a.b()`.

CALLS edges are written with `static=true, runtime=false`.

## File map

| File | Action | Responsibility |
|---|---|---|
| `livegraph/static_ts/__init__.py` | Create | Package marker. |
| `livegraph/static_ts/parser.py` | Create | `parse_source(bytes, jsx) -> Tree`; `has_errors`. |
| `livegraph/static_ts/qualnames.py` | Create | `qualified_name(file, dotted)` helper. |
| `livegraph/static_ts/extractor.py` | Create | Tree walker → `(defs, imports, raw_calls)`. Mirrors the Python `extractor.py` shape. |
| `livegraph/static_ts/tsconfig.py` | Create | Tiny tsconfig.json reader; resolves `paths` aliases. Optional. |
| `livegraph/static_ts/resolver.py` | Create | `resolve_imports`, `resolve_calls` — TS-specific. |
| `livegraph/discovery.py` | Modify | Add `discover_typescript_files(root)`; existing `discover_python_files` unchanged. |
| `livegraph/ingest.py` | Modify | `ingest_project` now dispatches to both pipelines; tracks per-language summary. |
| `livegraph/incremental.py` | Modify | `update_files` + `detect_changes` recognize TS extensions, route to the TS reingest path. |
| `livegraph/watch/filters.py` | Modify | `PathFilter` accepts TS extensions in addition to `.py`. |
| `livegraph/cli.py` | Modify | Add `--lang python|typescript` opt-in to `build`/`ingest`/`update`. Default auto. |
| `pyproject.toml` | Modify | Add `tree-sitter-typescript>=0.23` runtime dep. |
| `tests/fixtures/sample_project_ts/` | Create | Small TS project: 3-4 files with imports, classes, default export. |
| `tests/unit/test_static_ts_parser.py` | Create | Parser smoke + grammar selection. |
| `tests/unit/test_static_ts_extractor.py` | Create | Definitions / calls / imports extraction across sample inputs. |
| `tests/unit/test_static_ts_qualnames.py` | Create | Qname format. |
| `tests/unit/test_static_ts_resolver.py` | Create | Relative + tsconfig alias + bare-module resolution; call name matching. |
| `tests/unit/test_tsconfig.py` | Create | tsconfig.json reader. |
| `tests/unit/test_ingest_ts_dispatch.py` | Create | Auto-detect dispatch picks the right pipeline(s). |
| `tests/integration/test_typescript_integration.py` | Create | Real Neo4j: ingest `sample_project_ts` end-to-end; verify `find_symbol`, `find_callers`, `find_cycles`, `semantic_search` (if `[semantic]`) all work over TS symbols. |
| `README.md` | Modify | Add a "TypeScript / JavaScript" section. |

No changes to: `livegraph/graph/`, `livegraph/mcp/`, `livegraph/check/`,
`livegraph/history/`, `livegraph/semantic/` — the schema and read side
are language-agnostic.

## Error handling

| Source | Behavior |
|---|---|
| File fails to parse | Log `INFO`, write `:File {parse_error: true, content_hash}`, skip definition/import/call extraction (matches Python pipeline). |
| `tsconfig.json` malformed | Log `WARNING`, fall back to no aliases. Don't fail ingest. |
| Bare import to unknown package | Recorded as `:Module {kind: "external"}` — no warning. |
| `--lang typescript` on a Python-only project | Warning: "no TypeScript files found", exit 0. |
| `tree-sitter-typescript` import fails (env borked) | `ImportError` propagates with a clear message about reinstalling deps. |

## Phase 5 / 8 / 10 / 12 interaction

- **Phase 5 (`update`)**: `detect_changes` already classifies by file
  extension; we widen the discovery walk to include TS files, then
  `reingest_files` calls the appropriate per-language pipeline based
  on file extension.
- **Phase 8 (`watch`)**: `PathFilter` widens to include TS extensions.
  `update_files` dispatch covers the rest. SIGINT and backoff
  behavior unchanged.
- **Phase 10 (`ingest-history`)**: walks `*` files via `git log` —
  symbol attribution uses the existing `:Symbol` label, which TS
  Function/Method nodes carry. No changes needed.
- **Phase 12 (`check`)**: zero-touch. All checks are language-agnostic
  Cypher.

## Testing

### Sample project: `tests/fixtures/sample_project_ts/`

```
package.json
tsconfig.json     # with one path alias: "@/util": "src/util.ts"
src/
  calc.ts         # exports Calculator class
  util.ts         # exports normalize, formatDate
  index.ts        # default export, imports both, calls Calculator
tests/
  calc.test.ts    # imports Calculator, calls .add()
```

Used by both unit fixtures and the integration test.

### Unit
- `test_static_ts_parser.py`: parser loads, picks correct grammar by
  extension (`.tsx` → tsx grammar; `.ts` → typescript grammar).
- `test_static_ts_extractor.py`: extracts function declarations,
  arrow functions assigned to const, class methods, default exports.
  Captures direct calls and `new` expressions. Skips type-only
  declarations.
- `test_static_ts_qualnames.py`: `qualified_name("src/calc.ts",
  "Calculator.add") == "src/calc.ts::Calculator.add"`.
- `test_static_ts_resolver.py`: relative path with extension
  inference, tsconfig alias substitution, bare module passthrough.
  Call name matching against project-defined symbols.
- `test_tsconfig.py`: read sample tsconfig, extract paths + baseUrl;
  no file → empty config; malformed JSON → empty + warning.
- `test_ingest_ts_dispatch.py`: with a tmp project mixing `.py` and
  `.ts`, verify both pipelines run; `--lang python` skips TS;
  `--lang typescript` skips Python.

### Integration
- `test_typescript_integration.py` (`pytest.mark.integration`):
  ingest `sample_project_ts` into real Neo4j. Verify:
  - `:File` count matches sample files (excluding `node_modules` etc).
  - `Calculator` class + `add` method exist as `:Class` / `:Method`
    nodes with TS-style qnames.
  - `index.ts::default` calls `Calculator.add` (CALLS edge).
  - IMPORTS edges resolve via both relative paths and the `@/util`
    alias.
  - `find_cycles(scope="module")` over the sample shows no cycle.
  - `find_callers("src/calc.ts::Calculator.add")` returns the
    `index.ts` and `calc.test.ts` callers.

## Performance

`tree-sitter-typescript` is comparable to `tree-sitter-python` —
single-digit ms per file. A medium TS project (5k files) ingests in
~10-30s, same order as Python today. Writes batch through the
existing GraphWriter.

## Out of scope (future phases)

- Node runtime tracing (would need a separate tracing strategy
  comparable to Python's sys.monitoring).
- CommonJS `require()` resolution.
- Dynamic `import("…")` resolution.
- JSX component usages as CALLS edges (`<Button />` → `Button`).
- Decorators (`@Component`).
- Type-only declarations.
- Type-aware method resolution.
- `package.json` exports / conditional resolution.
- Yarn PnP / pnpm workspace symlink unwinding.
