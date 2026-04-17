"""Integration: Stage 1 (index) against the tiny_repo fixture.

Drives Stage 0 then Stage 1, verifies every file in the discovery tree has a
persisted signature JSON on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.stages.s0_discover import run as stage_discover
from designdoc.stages.s1_index import run as stage_index
from designdoc.state import PipelineState, StageStatus

TINY_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_repo"


@pytest.mark.anyio
async def test_stage1_against_tiny_repo(tmp_path: Path):
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)

    await stage_discover(state=state, exclude_paths=[])
    signatures = await stage_index(state=state)

    assert state.stages["index"] == StageStatus.DONE
    # tiny_repo has 5 Python files
    assert len(signatures) == 5

    # gateway.py should have exactly the StripeGateway class with 2 methods
    gateway_sig = next(s for s in signatures if "gateway.py" in s.path)
    assert gateway_sig.classes
    stripe = next(c for c in gateway_sig.classes if c.name == "StripeGateway")
    method_names = {m.name for m in stripe.methods}
    assert {"__init__", "charge", "refund"} <= method_names

    # Must be persisted to disk for later stages
    persisted = output / "stage1_signatures.json"
    assert persisted.exists()
    data = json.loads(persisted.read_text())
    assert len(data) == 5


@pytest.mark.anyio
async def test_stage1_requires_stage0_first(tmp_path: Path):
    """Stage 1 depends on Stage 0's output — verify it fails cleanly if missing."""
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)

    with pytest.raises(FileNotFoundError):
        await stage_index(state=state)
