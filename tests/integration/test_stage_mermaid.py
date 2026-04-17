"""Integration: Stage 5 mermaid generation against pre-seeded class docs.

Gated by requires_mmdc. Uses a FakeSDK that returns valid mermaid + pass
verdict, proving the full Stage 5 path end-to-end including mmdc validation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s5_mermaid import run as stage_mermaid
from designdoc.state import PipelineState, StageStatus


class FakeSDK:
    async def query(self, *, prompt: str, options: dict) -> dict:
        system = options.get("system_prompt") or ""
        if "mermaid diagram generator" in system:
            return {
                "text": "flowchart TD\n    A --> B\n",
                "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
            }
        if "mermaid-semantics reviewer" in system:
            return {
                "text": json.dumps({"status": "pass", "summary": "ok"}),
                "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0005},
            }
        raise AssertionError(f"unexpected agent: {system[:80]}")


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_stage5_appends_diagram_to_each_class_doc(tmp_path: Path):
    output = tmp_path / "design"
    classes_dir = output / "packages" / "payments" / "classes"
    classes_dir.mkdir(parents=True)
    (classes_dir / "Gateway.md").write_text("## Purpose\nGateway class.")
    (classes_dir / "Charge.md").write_text("## Purpose\nCharge class.")

    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())

    diagrams = await stage_mermaid(state=state, runner=runner)

    assert state.stages["mermaid"] == StageStatus.DONE
    assert len(diagrams) == 2
    for doc in classes_dir.glob("*.md"):
        body = doc.read_text()
        assert "## Diagram" in body
        assert "```mermaid" in body
        assert "flowchart TD" in body


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_stage5_requires_packages_dir(tmp_path: Path):
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())
    with pytest.raises(FileNotFoundError):
        await stage_mermaid(state=state, runner=runner)
