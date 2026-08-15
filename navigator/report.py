"""Human-facing output.

Everything here writes to stdout; structured logs go to stderr. Redirecting one
never swallows the other.
"""

from __future__ import annotations

from collections import Counter

from rich.console import Console
from rich.table import Table

from navigator.models import PipelineRun, Rejection
from navigator.pipeline.runner import STAGES, RunResult

console = Console()


def _ms(value: int) -> str:
    return f"{value / 1000:.2f}s" if value >= 1000 else f"{value}ms"


def stage_line(stage: str, detail: str, elapsed_ms: int) -> None:
    """Live progress, printed as each stage completes."""
    console.print(
        f"  [cyan]{stage:<9}[/cyan] {detail:<34} [dim]{_ms(elapsed_ms)}[/dim]"
    )


def run_summary(result: RunResult) -> None:
    table = Table(
        title=f"Run #{result.run_id}  ·  {result.status}",
        title_style="bold",
        header_style="bold",
        show_edge=False,
    )
    table.add_column("Stage")
    table.add_column("Rows", justify="right")
    table.add_column("Detail")
    table.add_column("Time", justify="right")

    table.add_row("extract", f"{result.rows_extracted:,}", "read from source",
                  _ms(result.timings.get("extract", 0)))
    table.add_row(
        "validate",
        f"{result.rows_valid:,}",
        f"[red]{result.rows_rejected:,} rejected[/red]" if result.rows_rejected
        else "no rejections",
        _ms(result.timings.get("validate", 0)),
    )
    table.add_row("transform", f"{result.rows_valid:,}", "cleaned and derived",
                  _ms(result.timings.get("transform", 0)))
    table.add_row(
        "load",
        f"{result.rows_inserted + result.rows_updated:,}",
        f"{result.rows_inserted:,} inserted, {result.rows_updated:,} updated",
        _ms(result.timings.get("load", 0)),
    )
    table.add_section()
    table.add_row("[bold]total[/bold]", "", "", f"[bold]{_ms(result.total_ms)}[/bold]")

    console.print()
    console.print(table)

    if result.rejections:
        _rejection_breakdown(result)


def _rejection_breakdown(result: RunResult) -> None:
    counts = Counter(rejection.rule for rejection in result.rejections)

    table = Table(title="Rejections by rule", title_style="bold",
                  header_style="bold", show_edge=False)
    table.add_column("Rule")
    table.add_column("Rows", justify="right")
    table.add_column("Example")

    examples = {}
    for rejection in result.rejections:
        examples.setdefault(rejection.rule, rejection)

    for rule, count in counts.most_common():
        example = examples[rule]
        table.add_row(
            rule,
            f"{count:,}",
            f"[dim]line {example.source_row}: {example.message}[/dim]",
        )

    console.print()
    console.print(table)


def status_table(runs: list[PipelineRun]) -> None:
    if not runs:
        console.print(
            "[yellow]No runs recorded yet.[/yellow] Run [bold]navigator run[/bold] first."
        )
        return

    table = Table(title="Recent runs", title_style="bold", header_style="bold",
                  show_edge=False)
    table.add_column("#", justify="right")
    table.add_column("Started (UTC)")
    table.add_column("Status")
    table.add_column("In", justify="right")
    table.add_column("Valid", justify="right")
    table.add_column("Rejected", justify="right")
    table.add_column("Ins", justify="right")
    table.add_column("Upd", justify="right")
    table.add_column("Time", justify="right")

    for run in runs:
        colour = {"success": "green", "failed": "red"}.get(run.status, "yellow")
        table.add_row(
            str(run.id),
            run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            f"[{colour}]{run.status}[/{colour}]",
            f"{run.rows_extracted:,}",
            f"{run.rows_valid:,}",
            f"{run.rows_rejected:,}" if not run.rows_rejected
            else f"[red]{run.rows_rejected:,}[/red]",
            f"{run.rows_inserted:,}",
            f"{run.rows_updated:,}",
            _ms(run.total_ms),
        )

    console.print()
    console.print(table)

    failures = [run for run in runs if run.status == "failed" and run.error]
    for run in failures:
        console.print(f"  [red]run #{run.id}[/red] [dim]{run.error}[/dim]")


def rejections_table(rows: list[Rejection], run_id: int) -> None:
    if not rows:
        console.print(f"[green]Run #{run_id} rejected no rows.[/green]")
        return

    table = Table(title=f"Rejected rows · run #{run_id}", title_style="bold",
                  header_style="bold", show_edge=False)
    table.add_column("Source line", justify="right")
    table.add_column("unique_key")
    table.add_column("Rule")
    table.add_column("Why")

    for row in rows:
        table.add_row(str(row.source_row), row.unique_key or "[dim]—[/dim]",
                      row.rule, row.message)

    console.print()
    console.print(table)
