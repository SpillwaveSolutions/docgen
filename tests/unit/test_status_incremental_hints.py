"""`designdoc status` should surface which stages are primed for
incremental skip on the next run — otherwise users have no visibility
into why their runs are (or aren't) reusing cached work.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from designdoc.cli import app
from designdoc.state import PipelineState, StageStatus

runner = CliRunner()


def _seed(output: Path, *, prev_hashes: dict[str, str], rollup_hashes: dict[str, str]) -> None:
    s = PipelineState.load_or_new(output_dir=output, target_repo=output.parent)
    s.stages["discover"] = StageStatus.DONE
    s.stages["finalize"] = StageStatus.DONE
    s.prev_hashes = prev_hashes
    s.rollup_hashes = rollup_hashes
    s.save()


def test_status_reports_incremental_readiness(tmp_path: Path):
    out = tmp_path / "design"
    _seed(
        out,
        prev_hashes={"a.py": "hash1", "b.py": "hash2"},
        rollup_hashes={
            "package:payments": "h1",
            "system:rollup": "h2",
            "tech_debt": "h3",
            "mermaid:packages/x/classes/A.md": "h4",
        },
    )

    result = runner.invoke(app, ["status", "--repo", str(tmp_path), "--output", str(out)])
    assert result.exit_code == 0
    body = result.stdout.lower()

    # Must mention the incremental cache contents so the user knows
    # what would skip on the next run.
    assert "prev_hashes" in body or "cached files" in body or "incremental" in body
    assert "2" in body  # count of prev_hashes entries
    # Rollup hash categories should surface
    assert "package" in body or "rollup" in body


def test_status_no_incremental_cache_reports_cold_state(tmp_path: Path):
    out = tmp_path / "design"
    _seed(out, prev_hashes={}, rollup_hashes={})

    result = runner.invoke(app, ["status", "--repo", str(tmp_path), "--output", str(out)])
    assert result.exit_code == 0
    body = result.stdout.lower()
    # Must indicate the next run will be a full cold regeneration
    assert "cold" in body or "no cached" in body or "prev_hashes: 0" in body
