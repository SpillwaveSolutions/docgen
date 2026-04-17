"""Tests for PipelineState — the resumable-on-crash state machine checkpoint.

Invariant: save() followed by load_or_new() must return a byte-identical state.
If this test ever regresses, resume correctness is broken.
"""

from __future__ import annotations

from pathlib import Path

from designdoc.state import PipelineState, StageStatus


def test_new_state_has_no_stages_and_zero_current(tmp_path: Path):
    s = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    assert s.stages == {}
    assert s.current_stage == 0
    assert s.hil_issues == []
    assert s.artifact_index == {}


def test_roundtrip_preserves_all_fields(tmp_path: Path):
    s = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    s.stages["discover"] = StageStatus.DONE
    s.stages["index"] = StageStatus.RUNNING
    s.current_stage = 1
    s.total_retries = 4
    s.hil_issues.append({"id": "HIL-001", "severity": "major"})
    s.artifact_index["pkg.ClassA"] = "packages/pkg/classes/ClassA.md"
    s.save()

    s2 = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    assert s2.stages["discover"] == StageStatus.DONE
    assert s2.stages["index"] == StageStatus.RUNNING
    assert s2.current_stage == 1
    assert s2.total_retries == 4
    assert s2.hil_issues == [{"id": "HIL-001", "severity": "major"}]
    assert s2.artifact_index == {"pkg.ClassA": "packages/pkg/classes/ClassA.md"}


def test_save_creates_output_dir_if_missing(tmp_path: Path):
    """PipelineState.save() must mkdir -p the output_dir — stages expect this side effect."""
    out = tmp_path / "nested" / "dir" / "that" / "does-not-exist"
    s = PipelineState(target_repo=Path("/x"), output_dir=out)
    s.save()
    assert out.exists()
    assert (out / ".designdoc-state.json").exists()


def test_stage_status_serializes_as_string(tmp_path: Path):
    s = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    s.stages["discover"] = StageStatus.DONE
    s.save()
    raw = (tmp_path / ".designdoc-state.json").read_text()
    assert '"done"' in raw
