"""Typer CLI surface.

Four subcommands:
- generate : run the full pipeline (stages 0-8)
- resume   : pick up from the last checkpoint
- status   : print current pipeline state + cost ledger
- resolve  : walk open HIL issues (wired in Task 22 via the plugin)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import anyio
import typer

from designdoc.budget import BUDGET_FILENAME, BudgetExceededError, CostAccumulator
from designdoc.config import load_config
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


def _configure_logging() -> None:
    """Configure root logger so orchestrator progress surfaces on stderr.

    Idempotent — skips if the root logger already has handlers (e.g.
    pytest's caplog fixture manages its own)."""
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(level=logging.INFO, format="%(message)s")


RepoOpt = Annotated[Path | None, typer.Option("--repo", help="Target repository path")]
OutputOpt = Annotated[
    Path | None, typer.Option("--output", help="Output dir (default <repo>/docs/design)")
]
ConfigOpt = Annotated[Path | None, typer.Option("--config", help="Path to .designdoc.toml")]
# Budget defaults to None so we can tell "user set it" vs "use config/default".
# Precedence: --budget flag > config.max_budget_usd > Config default (5.00).
BudgetOpt = Annotated[
    float | None,
    typer.Option("--budget", help="Max budget in USD (overrides config.max_budget_usd)"),
]
SkipOpt = Annotated[
    list[str] | None,
    typer.Option("--skip", help="Stage names to skip (repeatable)"),
]
ParallelismOpt = Annotated[
    int | None,
    typer.Option(
        "--parallelism",
        help="Max concurrent LLM calls per stage (overrides config.parallelism)",
    ),
]


def _resolve_repo(repo: Path | None) -> Path:
    return repo if repo is not None else Path.cwd()


def _resolve_output(
    repo: Path, output: Path | None, config_output_dir: str = "docs/design"
) -> Path:
    """Precedence: explicit --output wins; otherwise config.output_dir
    (resolved relative to repo); otherwise "docs/design"."""
    if output is not None:
        return output
    return repo / config_output_dir


async def _run_orchestrator(
    repo: Path,
    output_flag: Path | None,
    budget_usd: float | None,
    skip: set[str],
    config_path: Path | None,
    parallelism_flag: int | None,
) -> None:
    config = load_config(config_path) if config_path else load_config(None)
    # Precedence: --parallelism flag > config.parallelism > Config default (3).
    if parallelism_flag is not None:
        config = config.model_copy(update={"parallelism": parallelism_flag})
    output = _resolve_output(repo, output_flag, config.output_dir)
    # Precedence: explicit --budget wins; otherwise config value wins.
    cap_usd = budget_usd if budget_usd is not None else config.max_budget_usd
    state = PipelineState.load_or_new(output_dir=output, target_repo=repo)
    budget = CostAccumulator.load_or_new(cap_usd=cap_usd, path=output / BUDGET_FILENAME)
    runner = ClaudeSDKRunner(budget=budget)
    orchestrator = Orchestrator(
        state=state, runner=runner, budget=budget, config=config, skip_stages=skip
    )
    await orchestrator.run()


@app.command()
def generate(
    repo: RepoOpt = None,
    output: OutputOpt = None,
    config: ConfigOpt = None,
    budget: BudgetOpt = None,
    skip: SkipOpt = None,
    parallelism: ParallelismOpt = None,
) -> None:
    """Run the full pipeline (stages 0-8)."""
    _configure_logging()
    repo_p = _resolve_repo(repo)
    skip_set = set(skip or [])

    if parallelism is not None and parallelism < 1:
        typer.echo(f"--parallelism must be >= 1; got {parallelism}", err=True)
        raise typer.Exit(code=2)

    # Validate --config up front so a missing path produces a distinct
    # "config" error rather than conflating with stage-ordering errors
    # deep in the pipeline (both raise FileNotFoundError).
    if config is not None and not config.exists():
        typer.echo(f"--config path not found: {config}", err=True)
        raise typer.Exit(code=2)

    # Validate config surface early so bad diagram_format etc fail fast
    # rather than at the first stage that would care.
    try:
        load_config(config)
    except (ValueError, TypeError) as e:
        typer.echo(f"invalid config: {e}", err=True)
        raise typer.Exit(code=2) from e
    except Exception as e:
        # Pydantic ValidationError is a ValueError subclass but surfaces as
        # its own type; catch and map to exit 2 here.
        from pydantic import ValidationError

        if isinstance(e, ValidationError):
            typer.echo(f"invalid config: {e}", err=True)
            raise typer.Exit(code=2) from e
        raise

    try:
        anyio.run(_run_orchestrator, repo_p, output, budget, skip_set, config, parallelism)
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
    budget: BudgetOpt = None,
    skip: SkipOpt = None,
    parallelism: ParallelismOpt = None,
) -> None:
    """Resume from the last checkpoint. Identical code path as generate — the
    orchestrator skips DONE stages automatically."""
    generate(
        repo=repo,
        output=output,
        config=config,
        budget=budget,
        skip=skip,
        parallelism=parallelism,
    )


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

    # Incremental-regeneration readiness hints.
    _print_incremental_hints(state)

    budget_path = out / BUDGET_FILENAME
    if budget_path.exists():
        import json

        data = json.loads(budget_path.read_text())
        typer.echo(
            f"cost: ${data['total_cost_usd']:.4f} / cap ${data['cap_usd']:.2f} "
            f"({data['invocations']} invocations)"
        )


def _print_incremental_hints(state: PipelineState) -> None:
    """Report which caches are primed so the user knows which stages will
    skip on the next run. A cold run (empty prev_hashes + rollup_hashes)
    is called out explicitly."""
    prev_count = len(state.prev_hashes)
    rollup_count = len(state.rollup_hashes)
    if prev_count == 0 and rollup_count == 0:
        typer.echo("incremental: cold (no cached hashes — next run will regenerate everything)")
        return

    typer.echo(f"incremental: prev_hashes: {prev_count} file(s) cached")
    # Group rollup hashes by category prefix so the output stays
    # readable when there are many packages or mermaid artifacts.
    groups: dict[str, int] = {}
    for key in state.rollup_hashes:
        category = key.split(":", 1)[0] if ":" in key else key
        groups[category] = groups.get(category, 0) + 1
    for category, count in sorted(groups.items()):
        typer.echo(f"  rollup {category}: {count} cached")


EmitQuestionsOpt = Annotated[
    bool,
    typer.Option("--emit-questions", help="Print JSON describing the first open HIL issue"),
]
ApplyFixOpt = Annotated[
    str | None,
    typer.Option("--apply-fix", help="HIL id whose artifact should be patched"),
]
FixTextOpt = Annotated[
    str | None,
    typer.Option("--fix", help="Replacement text for --apply-fix"),
]


@app.command()
def resolve(
    repo: RepoOpt = None,
    output: OutputOpt = None,
    emit_questions: EmitQuestionsOpt = False,
    apply_fix: ApplyFixOpt = None,
    fix: FixTextOpt = None,
) -> None:
    """Walk open HIL issues.

    Two plugin-driven operations:
    - `--emit-questions` prints JSON for the slash command to consume.
    - `--apply-fix HIL-XXX --fix "<text>"` patches the affected artifact
      and marks the issue resolved.
    """
    from designdoc import resolve as _resolve
    from designdoc.stages.s8_finalize import HIL_FILENAME

    repo_path = _resolve_repo(repo)
    out = _resolve_output(repo_path, output)
    hil_yaml = out / HIL_FILENAME

    if emit_questions and apply_fix:
        typer.echo("--emit-questions and --apply-fix are mutually exclusive", err=True)
        raise typer.Exit(code=2)

    if emit_questions:
        typer.echo(_resolve.to_json(_resolve.emit_questions(hil_yaml, out)))
        return

    if apply_fix:
        if not fix:
            typer.echo("--fix <text> is required with --apply-fix", err=True)
            raise typer.Exit(code=2)
        result = _resolve.apply_fix(hil_yaml, out, hil_id=apply_fix, fix_text=fix)
        typer.echo(_resolve.to_json(result))
        if not result.get("applied"):
            raise typer.Exit(code=5)
        return

    typer.echo(
        "resolve requires --emit-questions or --apply-fix. "
        "Use the /designdoc resolve plugin for interactive walking."
    )
    raise typer.Exit(code=2)
