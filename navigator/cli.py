"""Command line interface."""

from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select

from navigator import __version__, report
from navigator.config import SOURCE_API, SOURCE_COLUMNS, settings
from navigator.db import SessionLocal, create_schema
from navigator.logging_conf import configure_logging
from navigator.models import PipelineRun, Rejection
from navigator.pipeline.extract import SchemaError
from navigator.pipeline.runner import run_pipeline

app = typer.Typer(
    help="Staged ETL pipeline for NYC 311 service requests.",
    no_args_is_help=True,
    add_completion=False,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"navigator {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True,
                           help="Show the version and exit.")
    ] = False,
    log_level: Annotated[
        str, typer.Option("--log-level", help="DEBUG, INFO, WARNING or ERROR.")
    ] = settings.log_level,
) -> None:
    configure_logging(log_level)


@app.command()
def run(
    source: Annotated[
        Path, typer.Option("--source", "-s", help="CSV file to ingest.")
    ] = settings.default_source,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Print only the final summary.")
    ] = False,
) -> None:
    """Run the pipeline: extract, validate, transform, load."""
    create_schema()

    report.console.print(f"\n[bold]navigator[/bold] [dim]{source}[/dim]")
    callback = None if quiet else report.stage_line

    try:
        result = run_pipeline(source, on_stage=callback)
    except SchemaError as exc:
        report.console.print(f"\n[red]Source rejected:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, then re-raised
        report.console.print(f"\n[red]Run failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc

    report.run_summary(result)

    if result.rows_rejected:
        report.console.print(
            f"\n[dim]Inspect them with:[/dim] navigator rejects --run {result.run_id}"
        )
    report.console.print()


@app.command()
def status(
    limit: Annotated[
        int, typer.Option("--limit", "-n", min=1, help="How many runs to show.")
    ] = 10,
) -> None:
    """Show the most recent pipeline runs."""
    create_schema()

    with SessionLocal() as session:
        runs = list(
            session.scalars(
                select(PipelineRun).order_by(PipelineRun.id.desc()).limit(limit)
            )
        )

    report.status_table(runs)
    report.console.print()


@app.command()
def rejects(
    run_id: Annotated[
        int | None, typer.Option("--run", help="Run to inspect. Defaults to the latest.")
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", "-n", min=1, help="Maximum rows to show.")
    ] = 20,
) -> None:
    """Show rows a run rejected, and why."""
    create_schema()

    with SessionLocal() as session:
        if run_id is None:
            run_id = session.scalar(
                select(PipelineRun.id).order_by(PipelineRun.id.desc()).limit(1)
            )
            if run_id is None:
                report.console.print(
                    "[yellow]No runs recorded yet.[/yellow] Run "
                    "[bold]navigator run[/bold] first."
                )
                raise typer.Exit()

        rows = list(
            session.scalars(
                select(Rejection)
                .where(Rejection.run_id == run_id)
                .order_by(Rejection.source_row)
                .limit(limit)
            )
        )
        total = session.scalar(
            select(Rejection.id).where(Rejection.run_id == run_id).limit(1)
        )

    if total is None and not rows:
        report.rejections_table([], run_id)
        report.console.print()
        raise typer.Exit()

    report.rejections_table(rows, run_id)
    if len(rows) == limit:
        report.console.print(f"[dim]  showing first {limit}; raise --limit for more[/dim]")
    report.console.print()


@app.command()
def fetch(
    limit: Annotated[
        int, typer.Option("--limit", "-n", min=1, max=50000, help="Rows to download.")
    ] = 4000,
    out: Annotated[
        Path, typer.Option("--out", "-o", help="Where to write the CSV.")
    ] = settings.default_source,
) -> None:
    """Download a fresh extract from the NYC Open Data API.

    Needs network access. The repository already ships a sample at data/raw.csv,
    so this is only for refreshing it.
    """
    query = urllib.parse.urlencode(
        {"$select": ",".join(SOURCE_COLUMNS), "$limit": str(limit)}
    )
    url = f"{SOURCE_API}?{query}"

    report.console.print(f"\n[bold]Downloading[/bold] [dim]{limit:,} rows[/dim]")
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read()
    except Exception as exc:  # noqa: BLE001 - network failure is user-facing
        report.console.print(f"[red]Download failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc

    out.write_bytes(payload)
    lines = payload.count(b"\n")
    report.console.print(
        f"[green]Wrote[/green] {out} [dim]({lines - 1:,} rows, "
        f"{len(payload) / 1024:.0f} KB)[/dim]\n"
    )


if __name__ == "__main__":
    app()
