"""Orchestrator log lines surface checkpoint counts per stage."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.orchestrator import Orchestrator, StageEntry
from designdoc.state import PipelineState


@pytest.mark.anyio
async def test_stage_log_includes_checkpoint_counts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    # Seed 3 checkpointed class_docs artifacts using the real key shape
    # Stage 3 writes: "<path>::<class_name>" (no prefix).
    for i in range(3):
        state.artifact_index[f"foo.py::C{i}"] = {
            "path": f"x/C{i}.md",
            "input_hash": f"h{i}",
        }
    budget = CostAccumulator(cap_usd=10.0, path=output / ".designdoc-budget.json")

    async def fake_stage(**_kwargs):
        return None

    orch = Orchestrator(
        state=state,
        runner=None,
        budget=budget,
        skip_stages={"mermaid"},
        stages=[StageEntry("class_docs", fake_stage, needs_runner=False)],
    )
    with caplog.at_level(logging.INFO):
        await orch.run()

    matching = [r.getMessage() for r in caplog.records if "class_docs" in r.getMessage()]
    starting = next((m for m in matching if "starting" in m or "checkpointed" in m), None)
    assert starting is not None, matching
    assert "3" in starting


@pytest.mark.anyio
async def test_stage_log_plain_starting_when_no_prior_checkpoints(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Stages with no prior artifacts keep the legacy "starting" line intact."""
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    budget = CostAccumulator(cap_usd=10.0, path=output / ".designdoc-budget.json")

    async def fake_stage(**_kwargs):
        return None

    orch = Orchestrator(
        state=state,
        runner=None,
        budget=budget,
        skip_stages={"mermaid"},
        stages=[StageEntry("file_analysis", fake_stage, needs_runner=False)],
    )
    with caplog.at_level(logging.INFO):
        await orch.run()

    matching = [r.getMessage() for r in caplog.records if "file_analysis" in r.getMessage()]
    starting = next((m for m in matching if "starting" in m), None)
    assert starting is not None, matching
    assert "checkpointed" not in starting
