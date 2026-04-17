"""Integration test for Stage 0 against the tiny_repo fixture.

Proves the full Stage 0 pipeline: read fixture, produce DiscoveryReport,
write checkpoint to state.json. No LLM calls — deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.stages.s0_discover import run as stage_discover
from designdoc.state import PipelineState, StageStatus

TINY_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_repo"


@pytest.mark.anyio
async def test_stage0_against_tiny_repo(tmp_path: Path):
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)

    report = await stage_discover(state=state, exclude_paths=[])

    # tiny_repo has 5 .py files: 3 __init__.py + gateway.py + report.py
    assert report.languages == {"python": 5}
    assert state.stages["discover"] == StageStatus.DONE

    # Verify the discovery report was persisted to disk
    persisted = output / "stage0_discovery.json"
    assert persisted.exists()
    data = json.loads(persisted.read_text())
    assert data["languages"]["python"] == 5


@pytest.mark.anyio
async def test_stage0_persists_tree_for_downstream_stages(tmp_path: Path):
    """Stage 1 needs stage 0's tree — verify it's readable from disk."""
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)

    await stage_discover(state=state, exclude_paths=[])

    persisted = output / "stage0_discovery.json"
    data = json.loads(persisted.read_text())
    tree_paths = set(data["tree"])
    # must include the two real source files
    assert any("gateway.py" in p for p in tree_paths)
    assert any("report.py" in p for p in tree_paths)
