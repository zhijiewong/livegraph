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
