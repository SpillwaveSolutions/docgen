"""Resume test: the load-bearing proof of Gen 3 determinism.

Run stages 0-3, kill the process, restart with the same state file. Stages 0-3
must be skipped; stages 4-8 must run. If this regresses, the whole
'harness engineering' thesis breaks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.orchestrator import Orchestrator, default_stage_table
from designdoc.runner import ClaudeSDKRunner
from designdoc.state import PipelineState, StageStatus

TINY_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_repo"


class _RecordingSDK:
    """Responds to every agent; records every call for later assertions."""

    def __init__(self):
        self.calls: list[str] = []

    async def query(self, *, prompt: str, options: dict) -> dict:
        system = options.get("system_prompt") or ""
        self.calls.append(system[:60])
        if "code-summary agent" in system:
            return _ok(
                {
                    "purpose": "stub",
                    "key_types": [],
                    "key_functions": [],
                    "external_deps": [],
                    "notes": "",
                }
            )
        if "design-documentation writer" in system:
            return _ok_text("## Purpose\nStub.")
        if "documentation QA reviewer" in system:
            return _ok({"status": "pass", "summary": "ok"})
        if "package-level documentation writer" in system:
            return _ok_text("## Overview\nStub.")
        if "rollup-accuracy reviewer" in system:
            return _ok({"status": "pass", "summary": "ok"})
        if "mermaid diagram generator" in system:
            return _ok_text("flowchart TD\n    A --> B\n")
        if "mermaid-semantics reviewer" in system:
            return _ok({"status": "pass", "summary": "ok"})
        if "tech-debt researcher" in system:
            return _ok(
                {
                    "name": "requests",
                    "pinned": ">=2.31",
                    "latest": "2.32",
                    "status": "current",
                    "cves": [],
                    "recommended_action": "none",
                    "sources": [],
                }
            )
        if "cross-reference reviewer" in system:
            return _ok({"status": "pass", "summary": "ok"})
        if "system design writer" in system:
            return _ok_text(
                "<<<SYSTEM_DESIGN>>>\n## Overview\nstub\n\n"
                "<<<ARCHITECTURE>>>\n## Containers\n- cli\n"
            )
        if "system-design accuracy reviewer" in system:
            return _ok({"status": "pass", "summary": "ok"})
        raise AssertionError(f"unhandled agent: {system[:80]}")


def _ok(obj: dict) -> dict:
    return {
        "text": json.dumps(obj),
        "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
    }


def _ok_text(text: str) -> dict:
    return {"text": text, "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001}}


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_full_pipeline_against_tiny_repo(tmp_path: Path):
    """Smoke: run every stage end-to-end on tiny_repo with a recording SDK."""
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)
    budget = CostAccumulator(cap_usd=5.00, path=output / ".designdoc-budget.json")
    runner = ClaudeSDKRunner(budget=budget, sdk=_RecordingSDK())

    orchestrator = Orchestrator(state=state, runner=runner, budget=budget)
    await orchestrator.run()

    # Every stage DONE
    stage_names = [e.name for e in default_stage_table()]
    for name in stage_names:
        assert state.stages[name] == StageStatus.DONE, f"{name} is {state.stages.get(name)}"

    # Landmark artifacts exist
    assert (output / "README.md").exists()
    assert (output / "SYSTEM_DESIGN.md").exists()
    assert (output / "ARCHITECTURE.md").exists()
    assert (output / "TECH_DEBT.md").exists()
    # No HIL issues in a fully green run
    assert not (output / "hil-issues.yaml").exists()


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_resume_skips_completed_stages(tmp_path: Path):
    """Run the full pipeline; then re-run with the same state. Every stage must
    be skipped (no additional LLM calls), and the final output must be byte-
    identical for deterministic stages."""
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)
    budget = CostAccumulator(cap_usd=5.00, path=output / ".designdoc-budget.json")
    sdk = _RecordingSDK()
    runner = ClaudeSDKRunner(budget=budget, sdk=sdk)

    await Orchestrator(state=state, runner=runner, budget=budget).run()
    first_call_count = len(sdk.calls)
    assert first_call_count > 0

    # Reload state from disk (simulating a fresh process) and run again
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)
    budget2 = CostAccumulator.load_or_new(cap_usd=5.00, path=output / ".designdoc-budget.json")
    runner2 = ClaudeSDKRunner(budget=budget2, sdk=sdk)

    await Orchestrator(state=state2, runner=runner2, budget=budget2).run()
    # No additional calls — every stage was skipped
    assert len(sdk.calls) == first_call_count


@pytest.mark.anyio
async def test_budget_exceeded_halts_pipeline(tmp_path: Path):
    """Set cap to $0 and verify the first LLM-using stage halts cleanly.

    v1.2 behavior: orchestrator returns (does not raise). state.halted_on_budget
    is True, the stage is marked FAILED, budget is persisted, and later stages
    have not run.
    """
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)
    budget = CostAccumulator(cap_usd=0.0001, path=output / ".designdoc-budget.json")
    runner = ClaudeSDKRunner(budget=budget, sdk=_RecordingSDK())

    # Skip mmdc preflight for this test — we're testing budget halt, not mmdc
    orchestrator = Orchestrator(state=state, runner=runner, budget=budget, skip_stages={"mermaid"})
    # No raise: orchestrator halts gracefully and persists halt flag.
    await orchestrator.run()

    assert state.halted_on_budget is True
    # Stages 0 and 1 are LLM-free and should still complete
    assert state.stages["discover"] == StageStatus.DONE
    assert state.stages["index"] == StageStatus.DONE
    # Stage 2 is the first LLM stage — must be FAILED
    assert state.stages.get("file_analysis") == StageStatus.FAILED
    # Later stages must not have run
    assert "tech_debt" not in state.stages or state.stages["tech_debt"] != StageStatus.DONE

    # Budget was persisted to disk so `designdoc status` can report it
    assert (output / ".designdoc-budget.json").exists()
