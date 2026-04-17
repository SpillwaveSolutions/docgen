"""Integration: Stage 2 (file analysis) against tiny_repo with a scripted runner.

No real API — uses a FakeSDK-driven runner that replays pre-baked responses for
each of the 5 tiny_repo Python files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s0_discover import run as stage_discover
from designdoc.stages.s1_index import run as stage_index
from designdoc.stages.s2_file_analysis import OUTPUT_FILENAME
from designdoc.stages.s2_file_analysis import run as stage_file_analysis
from designdoc.state import PipelineState, StageStatus

TINY_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_repo"


class FakeSDK:
    """Responds with a valid FileSummary JSON regardless of prompt."""

    def __init__(self):
        self.call_count = 0

    async def query(self, *, prompt: str, options: dict) -> dict:
        self.call_count += 1
        return {
            "text": json.dumps(
                {
                    "purpose": f"file {self.call_count}: stub summary",
                    "key_types": [],
                    "key_functions": [],
                    "external_deps": ["requests"],
                    "notes": "",
                }
            ),
            "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
        }


@pytest.mark.anyio
async def test_stage2_summarizes_every_indexed_file(tmp_path: Path):
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    fake = FakeSDK()
    budget = CostAccumulator(cap_usd=1.00)
    runner = ClaudeSDKRunner(budget=budget, sdk=fake)

    summaries = await stage_file_analysis(state=state, runner=runner)

    # tiny_repo has 5 .py files, all parseable
    assert len(summaries) == 5
    assert state.stages["file_analysis"] == StageStatus.DONE
    # Every summary must include "purpose"
    for s in summaries.values():
        assert s["purpose"]

    persisted = output / OUTPUT_FILENAME
    assert persisted.exists()
    data = json.loads(persisted.read_text())
    assert len(data) == 5


@pytest.mark.anyio
async def test_stage2_requires_stage1_first(tmp_path: Path):
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())

    with pytest.raises(FileNotFoundError):
        await stage_file_analysis(state=state, runner=runner)
