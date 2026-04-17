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


def test_generate_not_yet_wired_exits_nonzero(tmp_path: Path):
    """Until Task 20 wires the orchestrator, generate must exit with a clear message."""
    result = runner.invoke(app, ["generate", "--repo", str(tmp_path)])
    assert result.exit_code != 0
    assert "not yet" in result.stdout.lower() or "not yet" in (result.stderr or "").lower()


def test_resume_not_yet_wired_exits_nonzero(tmp_path: Path):
    result = runner.invoke(app, ["resume", "--repo", str(tmp_path)])
    assert result.exit_code != 0


def test_resolve_not_yet_wired_exits_nonzero(tmp_path: Path):
    result = runner.invoke(app, ["resolve", "--repo", str(tmp_path)])
    assert result.exit_code != 0
