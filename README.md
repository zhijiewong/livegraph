# livegraph

A runtime-augmented code knowledge graph for Python codebases.

`livegraph` builds a graph of a Python project in Neo4j, fusing static
tree-sitter analysis with runtime observation of the project's pytest
suite. Every `CALLS` edge is tagged with provenance (`static` / `runtime`)
so the graph reflects what the code *does*, not just what it looks like.

## Quick start

    docker compose up -d
    cp .env.example .env
    pip install -e ".[dev]"
    livegraph build /path/to/python/project

See `docs/superpowers/specs/` for the design.

## Using livegraph from a coding agent (MCP)

After `livegraph build /path/to/project`, expose the graph to a
coding agent over MCP:

```bash
LIVEGRAPH_PROJECT=myproject livegraph mcp
```

The server runs over stdio. Configure your MCP host (Claude Code, Cursor)
to launch it. Example `.mcp.json` for Claude Code:

```json
{
  "mcpServers": {
    "livegraph-myproject": {
      "command": "livegraph",
      "args": ["mcp", "--project", "myproject"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "livegraph-local"
      }
    }
  }
}
```

The server exposes 10 read-only tools, including the two that no
purely static code-graph tool can run:

| Tool | What it answers |
|---|---|
| `find_symbol(query)` | Symbols matching a name |
| `get_source(qualified_name)` | Source + coverage for a symbol |
| `find_callers(qualified_name, provenance)` | Who calls this — `static`/`runtime`/`any` |
| `find_callees(qualified_name, provenance)` | What this calls — same filter |
| **`runtime_only_calls(file?)`** | Calls runtime caught that static missed |
| `dead_static_calls(file?)` | Predicted calls that never executed |
| `tests_for(qualified_name)` | Tests that cover a symbol |
| `untested_symbols(file?, kind?)` | Functions/methods no test exercised |
| `imports(file, direction)` | File-level import edges |
| `graph_status()` | Aggregate counts; call this first |
| **`change_impact(diff, max_depth, provenance, limit)`** | Given a git diff: changed symbols, transitive callers with per-edge provenance, and the tests to run |

**Acceptance test:** with the server registered, ask your agent
"show me the dynamic-dispatch calls in this project". A working
integration finds and calls `runtime_only_calls`.

## Keeping the graph in sync (`livegraph update`)

After the first `livegraph build`, subsequent edits don't require a full rebuild.
Run:

```bash
LIVEGRAPH_PROJECT=myproject livegraph update
```

The command walks the project, computes SHA-256 hashes of every `.py` file,
compares against the hashes stored on `File` nodes, and re-ingests only the
files whose content actually changed. Deletions are removed from the graph;
new files are added; unchanged files are skipped.

Runtime data (from `livegraph trace`) is preserved on changed-file symbols
but flagged `runtime_stale=true`. CALLS edges that were already verified at
runtime survive incremental re-ingest. A subsequent `livegraph trace` clears
the stale flag on every symbol that appears in the new observations.

Use `--dry-run` to preview the classification without writing to the graph:

```bash
livegraph update --dry-run
```

Known limitation: a function renamed in file A while file B still calls it
by the old name leaves an orphaned `CALLS` edge until file B is also touched.
Run a full `livegraph build` to fully recover.
