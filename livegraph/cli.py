"""livegraph command-line interface."""
from __future__ import annotations

import logging

import typer

from livegraph.augment import augment_from_observations
from livegraph.config import load_settings
from livegraph.graph.backend import GraphBackend, Neo4jBackend
from livegraph.ingest import ingest_project
from livegraph.mcp.server import run_stdio
from livegraph.runtime.runner import RuntimeUnavailable, run_pytest

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


@app.command()
def trace(
    path: str = typer.Argument(..., help="Project root to trace"),
    python: str = typer.Option(None, "--python", help="Target interpreter"),
) -> None:
    """Phase 2: trace the project's pytest suite and augment the graph."""
    settings = load_settings()
    backend = _make_backend()
    try:
        observations = run_pytest(path, python=python)
        summary = augment_from_observations(
            observations, backend, batch_size=settings.livegraph_batch_size)
    except RuntimeUnavailable as exc:
        typer.echo(f"Phase 2 skipped: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        backend.close()
    typer.echo(
        f"Phase 2 complete: {summary.runtime_call_edges} runtime call "
        f"edges, {summary.tests} tests, {summary.coverage_edges} coverage "
        f"edges."
    )


@app.command()
def build(
    path: str = typer.Argument(..., help="Project root to build"),
    python: str = typer.Option(None, "--python", help="Target interpreter"),
) -> None:
    """Run Phase 1 then Phase 2 for the project at PATH."""
    settings = load_settings()
    backend = _make_backend()
    try:
        ingest_summary = ingest_project(
            path, backend, batch_size=settings.livegraph_batch_size)
        typer.echo(
            f"Phase 1 complete: {ingest_summary.files} files, "
            f"{ingest_summary.definitions} definitions."
        )
        try:
            observations = run_pytest(path, python=python)
            augment_summary = augment_from_observations(
                observations, backend,
                batch_size=settings.livegraph_batch_size)
            typer.echo(
                f"Phase 2 complete: "
                f"{augment_summary.runtime_call_edges} runtime call edges, "
                f"{augment_summary.tests} tests."
            )
        except RuntimeUnavailable as exc:
            typer.echo(f"Phase 2 skipped: {exc}", err=True)
    finally:
        backend.close()


@app.command()
def mcp(
    project: str = typer.Option(
        None, "--project",
        help="Ingested project to serve (overrides LIVEGRAPH_PROJECT env)",
    ),
) -> None:
    """Run the MCP server over stdio."""
    settings = load_settings()
    resolved = project or settings.livegraph_project
    if not resolved:
        typer.echo(
            "LIVEGRAPH_PROJECT is not set. Pass --project NAME or set the "
            "LIVEGRAPH_PROJECT environment variable to the name of an "
            "ingested project.",
            err=True,
        )
        raise typer.Exit(code=2)
    backend = _make_backend()
    try:
        backend.verify()
    except ConnectionError as exc:
        typer.echo(f"Neo4j unreachable: {exc}", err=True)
        backend.close()
        raise typer.Exit(code=1) from exc
    try:
        run_stdio(backend, resolved)
    finally:
        backend.close()


if __name__ == "__main__":  # pragma: no cover
    app()
