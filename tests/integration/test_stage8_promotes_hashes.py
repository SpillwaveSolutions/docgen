"""Stage 8 must promote Stage 0's current hashes into state.prev_hashes
at finalize, so the NEXT run has a baseline to diff against.
"""

from __future__ import annotations

import pytest

from designdoc.stages.s0_discover import run as stage_discover
from designdoc.stages.s8_finalize import run as stage_finalize
from designdoc.state import PipelineState


@pytest.mark.anyio
async def test_finalize_promotes_current_hashes(tmp_path):
    # seed a couple of source files
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y = 2\n")

    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    await stage_discover(state=state, exclude_paths=[])

    # Stage 0 doesn't populate prev_hashes — that's Stage 8's job
    assert state.prev_hashes == {}

    await stage_finalize(state=state)

    # After finalize, prev_hashes should match what Stage 0 wrote
    assert "a.py" in state.prev_hashes
    assert "sub/b.py" in state.prev_hashes
    assert len(state.prev_hashes["a.py"]) == 40  # SHA1


@pytest.mark.anyio
async def test_finalize_no_op_when_stage0_skipped(tmp_path):
    """If Stage 0 output is missing, finalize must not crash — it just skips
    the hash promotion."""
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await stage_finalize(state=state)  # must not raise
    assert state.prev_hashes == {}
