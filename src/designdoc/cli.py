"""Typer CLI surface.

Four subcommands:
- generate : run the full pipeline (stages 0-8)
- resume   : pick up from the last checkpoint
- status   : print current pipeline state + cost ledger
- resolve  : walk open HIL issues (wired in Task 22 via the plugin)
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import anyio
import typer

from designdoc.budget import BUDGET_FILENAME, BudgetExceededError, CostAccumulator
from designdoc.mermaid.mmdc import MmdcNotAvailableError
from designdoc.orchestrator import Orchestrator
from designdoc.runner import ClaudeSDKRunner
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
SkipOpt = Annotated[
    list[str] | None,
    typer.Option("--skip", help="Stage names to skip (repeatable)"),
]


def _resolve_repo(repo: Path | None) -> Path:
    return repo if repo is not None else Path.cwd()


def _resolve_output(repo: Path, output: Path | None) -> Path:
    return output if output is not None else repo / "docs" / "design"


async def _run_orchestrator(repo: Path, output: Path, budget_usd: float, skip: set[str]) -> None:
    state = PipelineState.load_or_new(output_dir=output, target_repo=repo)
    budget = CostAccumulator.load_or_new(cap_usd=budget_usd, path=output / BUDGET_FILENAME)
    runner = ClaudeSDKRunner(budget=budget)
    orchestrator = Orchestrator(state=state, runner=runner, budget=budget, skip_stages=skip)
    await orchestrator.run()


@app.command()
def generate(
    repo: RepoOpt = None,
    output: OutputOpt = None,
    config: ConfigOpt = None,
    budget: BudgetOpt = 5.00,
    skip: SkipOpt = None,
) -> None:
    """Run the full pipeline (stages 0-8)."""
    repo_p = _resolve_repo(repo)
    out = _resolve_output(repo_p, output)
    skip_set = set(skip or [])
    try:
        anyio.run(_run_orchestrator, repo_p, out, budget, skip_set)
    except MmdcNotAvailableError as e:
        typer.echo(f"mmdc preflight failed: {e}", err=True)
        typer.echo("Use --skip mermaid to proceed without mermaid diagrams.", err=True)
        raise typer.Exit(code=3) from e
    except BudgetExceededError as e:
        typer.echo(f"budget exceeded: {e}", err=True)
        typer.echo("Run `designdoc status` to see the last completed stage.", err=True)
        raise typer.Exit(code=4) from e


@app.command()
def resume(
    repo: RepoOpt = None,
    output: OutputOpt = None,
    config: ConfigOpt = None,
    budget: BudgetOpt = 5.00,
    skip: SkipOpt = None,
) -> None:
    """Resume from the last checkpoint. Identical code path as generate — the
    orchestrator skips DONE stages automatically."""
    generate(repo=repo, output=output, config=config, budget=budget, skip=skip)


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

    budget_path = out / BUDGET_FILENAME
    if budget_path.exists():
        import json

        data = json.loads(budget_path.read_text())
        typer.echo(
            f"cost: ${data['total_cost_usd']:.4f} / cap ${data['cap_usd']:.2f} "
            f"({data['invocations']} invocations)"
        )


@app.command()
def resolve(
    repo: RepoOpt = None,
    output: OutputOpt = None,
) -> None:
    """Walk open HIL issues using AskUserQuestion (plugin-driven in Task 22)."""
    typer.echo("resolve is not yet wired — Task 22 implements the HIL walker.")
    raise typer.Exit(code=2)
