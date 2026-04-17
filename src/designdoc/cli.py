"""Typer CLI surface.

Four subcommands:
- generate : run the full pipeline (wired in Task 20 when the orchestrator lands)
- resume   : pick up from the last checkpoint (Task 20)
- status   : print current pipeline state + cost ledger (implemented now)
- resolve  : walk open HIL issues (Task 22)

Unimplemented subcommands exit with a clear "not yet wired" message rather
than silently no-op'ing — callers see the wall.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from designdoc.state import STATE_FILENAME, PipelineState

app = typer.Typer(
    name="designdoc",
    help="Harness-engineered codebase documentation pipeline.",
    no_args_is_help=True,
    add_completion=False,
)


RepoOpt = Annotated[Path | None, typer.Option("--repo", help="Target repository path")]
OutputOpt = Annotated[
    Path | None, typer.Option("--output", help="Output dir (default <repo>/docs/design)")
]
ConfigOpt = Annotated[Path | None, typer.Option("--config", help="Path to .designdoc.toml")]
BudgetOpt = Annotated[float, typer.Option("--budget", help="Max budget in USD")]


def _resolve_repo(repo: Path | None) -> Path:
    return repo if repo is not None else Path.cwd()


def _resolve_output(repo: Path, output: Path | None) -> Path:
    return output if output is not None else repo / "docs" / "design"


@app.command()
def generate(
    repo: RepoOpt = None,
    output: OutputOpt = None,
    config: ConfigOpt = None,
    budget: BudgetOpt = 5.00,
) -> None:
    """Run the full pipeline (stages 0-8)."""
    typer.echo("generate is not yet wired — the orchestrator lands in Task 20.")
    raise typer.Exit(code=2)


@app.command()
def resume(
    repo: RepoOpt = None,
    output: OutputOpt = None,
    config: ConfigOpt = None,
    budget: BudgetOpt = 5.00,
) -> None:
    """Resume from the last checkpoint."""
    typer.echo("resume is not yet wired — the orchestrator lands in Task 20.")
    raise typer.Exit(code=2)


@app.command()
def status(
    repo: RepoOpt = None,
    output: OutputOpt = None,
) -> None:
    """Print pipeline state and cost ledger for the target repo."""
    repo_path = _resolve_repo(repo)
    out = _resolve_output(repo_path, output)
    state_path = out / STATE_FILENAME
    if not state_path.exists():
        typer.echo(f"no state found at {state_path} (pipeline has not run)")
        raise typer.Exit(code=0)

    state = PipelineState.load_or_new(output_dir=out, target_repo=repo_path)
    typer.echo(f"repo: {state.target_repo}")
    typer.echo(f"output: {state.output_dir}")
    typer.echo(f"current_stage: {state.current_stage}")
    typer.echo("stages:")
    for name, status_val in state.stages.items():
        typer.echo(f"  {name}: {status_val}")
    typer.echo(f"hil_issues: {len(state.hil_issues)}")
    typer.echo(f"total_retries: {state.total_retries}")


@app.command()
def resolve(
    repo: RepoOpt = None,
    output: OutputOpt = None,
) -> None:
    """Walk open HIL issues using AskUserQuestion (wired by the plugin in Task 22)."""
    typer.echo("resolve is not yet wired — Task 22 implements the HIL walker.")
    raise typer.Exit(code=2)
