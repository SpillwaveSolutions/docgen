"""Integration: Stage 3 against tiny_repo with scripted doer + checker.

The FakeSDK returns distinct responses by agent name: markdown for the doer,
JSON verdict for the checker.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s0_discover import run as stage_discover
from designdoc.stages.s1_index import run as stage_index
from designdoc.stages.s3_class_docs import run as stage_class_docs
from designdoc.state import PipelineState, StageStatus

TINY_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_repo"


class FakeSDK:
    """Returns markdown for the documenter, pass-JSON for the checker."""

    def __init__(self):
        self.calls: list[str] = []

    async def query(self, *, prompt: str, options: dict) -> dict:
        system = options.get("system_prompt") or ""
        # Agents distinguish themselves by their system prompt
        if "design-documentation writer" in system:
            self.calls.append("doer")
            return {
                "text": "## Purpose\nStub class doc.\n\n## Public API\n- method(): stub\n",
                "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
            }
        if "documentation QA reviewer" in system:
            self.calls.append("checker")
            return {
                "text": json.dumps({"status": "pass", "summary": "ok"}),
                "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0005},
            }
        raise AssertionError(f"unexpected system prompt: {system[:60]}")


@pytest.mark.anyio
async def test_stage3_produces_one_doc_per_class(tmp_path: Path):
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())
    written = await stage_class_docs(state=state, runner=runner)

    assert state.stages["class_docs"] == StageStatus.DONE
    # tiny_repo has 2 classes: StripeGateway, Report (plus Charge which is a dataclass)
    # Charge is a dataclass so ast still picks it up — expect 3.
    assert len(written) >= 2
    assert any("StripeGateway" in k for k in written)
    assert any("Report" in k for k in written)

    # All docs written to disk under packages/<pkg>/classes/
    for rel_path in written.values():
        assert (output / rel_path).exists()
        assert (output / rel_path).read_text().startswith("## ") or "HIL" in (
            output / rel_path
        ).read_text()


@pytest.mark.anyio
async def test_stage3_calls_both_doer_and_checker_in_isolation(tmp_path: Path):
    """Ensure doer and checker are separate calls — no self-grading."""
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    fake = FakeSDK()
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=fake)
    await stage_class_docs(state=state, runner=runner)

    # Must see both doer and checker calls, in alternation per class
    assert "doer" in fake.calls
    assert "checker" in fake.calls
    # Roughly equal counts — each class gets both roles
    doer_count = fake.calls.count("doer")
    checker_count = fake.calls.count("checker")
    assert doer_count == checker_count
