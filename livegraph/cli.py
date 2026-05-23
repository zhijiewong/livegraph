"""livegraph command-line interface."""
from __future__ import annotations

import logging

import typer

from livegraph.config import load_settings
from livegraph.graph.backend import GraphBackend, Neo4jBackend
from livegraph.ingest import ingest_project

app = typer.Typer(help="A runtime-augmented code knowledge graph for Python.")


def _make_backend() -> GraphBackend:
    """Build a graph backend from configuration (patched in tests)."""
    settings = load_settings()
    logging.basicConfig(level=settings.livegraph_log_level)
    return Neo4jBackend(settings.neo4j_uri, settings.neo4j_user,
                        settings.neo4j_password)


@app.command()
def ingest(path: str = typer.Argument(..., help="Project root to ingest")) -> None:
    """Phase 1: build the static graph for the project at PATH."""
    settings = load_settings()
    backend = _make_backend()
    try:
        summary = ingest_project(path, backend,
                                 batch_size=settings.livegraph_batch_size)
    finally:
        backend.close()
    typer.echo(
        f"Phase 1 complete: {summary.files} files, "
        f"{summary.definitions} definitions, {summary.call_edges} static "
        f"call edges, {summary.parse_errors} parse errors."
    )


@app.command()
def status() -> None:
    """Show node counts in the graph."""
    backend = _make_backend()
    try:
        rows = backend.execute(
            "MATCH (n) UNWIND labels(n) AS label "
            "RETURN label, count(*) AS n ORDER BY label"
        )
    finally:
        backend.close()
    if not rows:
        typer.echo("Graph is empty.")
        return
    for row in rows:
        typer.echo(f"{row['label']}: {row['n']}")


@app.command()
def clean(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation"),
) -> None:
    """Delete every node and relationship in the graph."""
    if not yes:
        typer.confirm("Delete the entire graph?", abort=True)
    backend = _make_backend()
    try:
        backend.execute("MATCH (n) DETACH DELETE n")
    finally:
        backend.close()
    typer.echo("Graph cleared.")


if __name__ == "__main__":  # pragma: no cover
    app()
