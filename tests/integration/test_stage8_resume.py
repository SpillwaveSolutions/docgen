"""Stage 8 resume coverage (T4.4).

Stage 8 is deterministic — no LLM, no doer/checker loop. The orchestrator
skips it via StageStatus.DONE rather than via artifact_index. These tests
lock in the resume contract:

- Fresh run writes README.md and (when HIL issues exist) hil-issues.yaml.
- A second run after Stage 0's hash file is unchanged is idempotent — same
  outputs, no errors.
- A run with no HIL issues correctly omits hil-issues.yaml from the
  reported return value.

Closes the asymmetry where stages 2-7 each have a resume test but stage 8
did not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.stages.s8_finalize import HIL_FILENAME, README_FILENAME
from designdoc.stages.s8_finalize import run as stage_finalize
from designdoc.state import PipelineState, StageStatus


def _seed_stage0(output: Path, hashes: dict[str, str]) -> None:
    """Write a minimal Stage 0 output the finalizer can promote into prev_hashes."""
    output.mkdir(parents=True, exist_ok=True)
    (output / STAGE0_FILENAME).write_text(
        json.dumps({"tree": list(hashes.keys()), "hashes": hashes})
    )


@pytest.mark.anyio("asyncio")
async def test_stage8_fresh_run_writes_readme(tmp_path: Path) -> None:
    """First run on an output dir produces README.md and marks the stage DONE."""
    output = tmp_path / "design"
    _seed_stage0(output, {"src/foo.py": "abc123"})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    result = await stage_finalize(state=state)

    assert (output / README_FILENAME).exists(), "README.md must be written"
    assert state.stages["finalize"] == StageStatus.DONE
    assert result["readme"] == README_FILENAME
    # No HIL issues seeded → hil_path empty in return
    assert result["hil"] == ""


@pytest.mark.anyio("asyncio")
async def test_stage8_emits_hil_yaml_when_issues_present(tmp_path: Path) -> None:
    """If state.hil_issues is non-empty at finalize, hil-issues.yaml ships."""
    output = tmp_path / "design"
    _seed_stage0(output, {"src/foo.py": "abc"})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    state.hil_issues.append(
        {
            "id": "HIL-001",
            "artifact": "file:src/foo.py",
            "stage": "file_analysis",
            "severity": "major",
            "doer_said": "draft",
            "checker_said": "rejected",
            "attempts": 3,
            "status": "open",
            "suggested_fixes": ["look again"],
        }
    )

    result = await stage_finalize(state=state)

    assert (output / HIL_FILENAME).exists(), "hil-issues.yaml must be written when HIL present"
    assert result["hil"] == HIL_FILENAME


@pytest.mark.anyio("asyncio")
async def test_stage8_rerun_is_idempotent(tmp_path: Path) -> None:
    """Two runs on identical inputs produce identical README.md content.
    Stage 8 is deterministic — re-running it is safe."""
    output = tmp_path / "design"
    _seed_stage0(output, {"src/foo.py": "abc"})
    state1 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await stage_finalize(state=state1)
    first = (output / README_FILENAME).read_text()

    # Reload state fresh (simulating a new process) and re-run.
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    await stage_finalize(state=state2)
    second = (output / README_FILENAME).read_text()

    assert first == second, "stage 8 must be idempotent given identical input"


@pytest.mark.anyio("asyncio")
async def test_stage8_promotes_stage0_hashes_to_prev_hashes(tmp_path: Path) -> None:
    """After finalize, state.prev_hashes mirrors stage 0's hashes — this is the
    incremental-regeneration baseline for the NEXT run."""
    output = tmp_path / "design"
    expected_hashes = {"src/foo.py": "hash-foo", "src/bar.py": "hash-bar"}
    _seed_stage0(output, expected_hashes)
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await stage_finalize(state=state)

    assert state.prev_hashes == expected_hashes, (
        "stage 8 must promote stage 0's hashes into state.prev_hashes for incremental resume"
    )
