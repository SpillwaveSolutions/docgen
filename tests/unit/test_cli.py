"""Unit tests for the typer CLI surface."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from designdoc.cli import app

runner = CliRunner()


def test_help_shows_four_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("generate", "resume", "status", "resolve"):
        assert cmd in result.stdout


def test_status_on_fresh_repo_prints_no_state(tmp_path: Path):
    """status on a repo with no prior pipeline run must exit 0 with a clear message."""
    result = runner.invoke(app, ["status", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "no state" in result.stdout.lower()


def test_status_reads_existing_state(tmp_path: Path):
    """status on a repo with a state file must report the captured stage info."""
    from designdoc.state import PipelineState, StageStatus

    out = tmp_path / "docs" / "design"
    s = PipelineState.load_or_new(output_dir=out, target_repo=tmp_path)
    s.stages["discover"] = StageStatus.DONE
    s.stages["index"] = StageStatus.RUNNING
    s.save()

    result = runner.invoke(app, ["status", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "discover" in result.stdout
    assert "done" in result.stdout.lower()


def test_generate_is_wired_to_orchestrator(tmp_path: Path):
    """generate must attempt to run the pipeline rather than print 'not yet wired'.

    On an empty target the pipeline fails downstream (no packages) — we only
    care here that the CLI actually invoked the orchestrator, which we detect
    by the presence of a state file written by Stage 0.
    """
    result = runner.invoke(app, ["generate", "--repo", str(tmp_path), "--skip", "mermaid"])
    # Stage 0 runs and checkpoints before Stage 4/7 error on missing packages dir
    from designdoc.state import STATE_FILENAME

    state_path = tmp_path / "docs" / "design" / STATE_FILENAME
    assert state_path.exists(), f"orchestrator didn't start: {result.output}"


def test_resume_is_wired_to_orchestrator(tmp_path: Path):
    """resume uses the same orchestrator call as generate (skips DONE stages)."""
    from designdoc.state import STATE_FILENAME

    runner.invoke(app, ["generate", "--repo", str(tmp_path), "--skip", "mermaid"])
    assert (tmp_path / "docs" / "design" / STATE_FILENAME).exists()
    # Second run must not crash with an unrelated exception. An empty tmp_path
    # leads to FileNotFoundError on missing packages dir (Stage 4); the CLI
    # maps that to exit code 2 via typer.Exit. Accept either the mapped exit
    # or a bubbled FileNotFoundError — both mean "resume invoked the orchestrator".
    result = runner.invoke(app, ["resume", "--repo", str(tmp_path), "--skip", "mermaid"])
    exc = result.exception
    if exc is not None:
        if isinstance(exc, SystemExit):
            assert exc.code in (0, 2), f"unexpected resume exit code: {exc.code}"
        else:
            assert "packages dir" in str(exc) or "stage" in str(exc).lower(), (
                f"resume crashed with unrelated exception: {exc!r}"
            )


def test_resolve_not_yet_wired_exits_nonzero(tmp_path: Path):
    """resolve ships in Task 22."""
    result = runner.invoke(app, ["resolve", "--repo", str(tmp_path)])
    assert result.exit_code != 0
