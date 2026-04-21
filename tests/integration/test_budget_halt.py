"""Graceful budget halt: mid-stage cap exit 0, resume --budget picks up."""

from __future__ import annotations

from pathlib import Path

import pytest

from designdoc.budget import BudgetExceededError, CostAccumulator
from designdoc.orchestrator import Orchestrator, StageEntry
from designdoc.state import PipelineState, StageStatus


class _AlwaysBudget:
    """Stage that immediately raises BudgetExceededError."""

    async def run(self, **_kwargs) -> None:
        raise BudgetExceededError("stub: cap exceeded")


@pytest.mark.anyio
async def test_orchestrator_catches_budget_and_returns_halted(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    budget = CostAccumulator(cap_usd=1.0, path=output / ".designdoc-budget.json")
    stage = _AlwaysBudget()

    orch = Orchestrator(
        state=state,
        runner=None,
        budget=budget,
        skip_stages={"mermaid"},
        stages=[StageEntry("fake", stage.run, needs_runner=False)],
    )
    # Instead of raising, the orchestrator returns None and marks FAILED.
    await orch.run()
    assert state.stages["fake"] == StageStatus.FAILED
    assert state.halted_on_budget is True


@pytest.mark.anyio
async def test_orchestrator_completed_has_halted_on_budget_false(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    budget = CostAccumulator(cap_usd=1.0, path=output / ".designdoc-budget.json")

    class _Noop:
        async def run(self, **_kwargs) -> None:
            return

    orch = Orchestrator(
        state=state,
        runner=None,
        budget=budget,
        skip_stages={"mermaid"},
        stages=[StageEntry("noop", _Noop().run, needs_runner=False)],
    )
    await orch.run()
    assert state.halted_on_budget is False
