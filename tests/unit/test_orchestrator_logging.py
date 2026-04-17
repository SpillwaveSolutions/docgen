"""The orchestrator logs stage-level progress so users see something
during long runs. Without this, a 26-minute cold run is silent.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.orchestrator import Orchestrator, StageEntry
from designdoc.runner import ClaudeSDKRunner
from designdoc.state import PipelineState


async def _stub_deterministic(**_kwargs):
    pass


async def _stub_with_runner(**_kwargs):
    pass


@pytest.mark.anyio
async def test_orchestrator_logs_stage_start_and_finish(tmp_path, caplog):
    """Every stage that runs must log a recognizable start and finish line."""
    state = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=_FakeSDK())

    table = [
        StageEntry("discover", _stub_deterministic, needs_runner=False),
        StageEntry("index", _stub_deterministic, needs_runner=False),
    ]
    orchestrator = Orchestrator(
        state=state,
        runner=runner,
        budget=CostAccumulator(cap_usd=1.0),
        stages=table,
        skip_stages={"mermaid"},  # skip mermaid preflight
    )

    with caplog.at_level(logging.INFO, logger="designdoc.orchestrator"):
        await orchestrator.run()

    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "discover" in messages
    assert "index" in messages
    # Start marker
    assert "start" in messages.lower() or "starting" in messages.lower()
    # Finish marker (duration in seconds or similar)
    assert "done" in messages.lower() or "complete" in messages.lower()


@pytest.mark.anyio
async def test_orchestrator_logs_skipped_stage(tmp_path, caplog):
    from designdoc.state import StageStatus

    state = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    state.stages["discover"] = StageStatus.DONE
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=_FakeSDK())

    table = [StageEntry("discover", _stub_deterministic, needs_runner=False)]
    orchestrator = Orchestrator(
        state=state,
        runner=runner,
        budget=CostAccumulator(cap_usd=1.0),
        stages=table,
        skip_stages={"mermaid"},
    )

    with caplog.at_level(logging.INFO, logger="designdoc.orchestrator"):
        await orchestrator.run()

    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "discover" in messages
    assert "skip" in messages.lower() or "already done" in messages.lower()


class _FakeSDK:
    async def query(self, *, prompt, options):
        return {"text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}
