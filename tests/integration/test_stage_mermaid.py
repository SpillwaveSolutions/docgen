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


class FakeSDKClassDiagram:
    """Returns classDiagram-style mermaid (vs FakeSDK's flowchart). Needed
    for the per-package merger test — the merger ignores non-classDiagram
    blocks by design, so the integration test must produce real classDiagram
    output."""

    def __init__(self):
        self._idx = 0

    async def query(self, *, prompt: str, options: dict) -> dict:
        system = options.get("system_prompt") or ""
        if "mermaid diagram generator" in system:
            # Alternate two different class diagrams so the merge has
            # something to deduplicate.
            self._idx += 1
            if self._idx % 2 == 1:
                text = "classDiagram\n    class Gateway\n    class Card\n    Gateway --> Card\n"
            else:
                text = "classDiagram\n    class Charge\n    class Card\n    Charge --> Card\n"
            return {
                "text": text,
                "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
            }
        if "mermaid-semantics reviewer" in system:
            import json

            return {
                "text": json.dumps({"status": "pass", "summary": "ok"}),
                "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0005},
            }
        raise AssertionError(f"unexpected agent: {system[:80]}")


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_stage5_appends_package_overview_diagram_to_readme(tmp_path: Path):
    """Stage 5 emits a per-package overview diagram (merged from the
    per-class diagrams) and appends it to each package README. Without this,
    package READMEs ship diagram-less and readers must drill into individual
    class docs to see any structural view of the package.
    """
    output = tmp_path / "design"
    pkg_dir = output / "packages" / "payments"
    classes_dir = pkg_dir / "classes"
    classes_dir.mkdir(parents=True)
    (classes_dir / "Gateway.md").write_text("## Purpose\nGateway class.")
    (classes_dir / "Charge.md").write_text("## Purpose\nCharge class.")
    # The package README must already exist (Stage 4 produces it). Pre-seed
    # one without a Diagram section to mirror the real Stage 4 output.
    (pkg_dir / "README.md").write_text(
        "# payments\n\n## Overview\nHandles money. No diagram here yet.\n"
    )

    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDKClassDiagram())

    await stage_mermaid(state=state, runner=runner)

    readme_text = (pkg_dir / "README.md").read_text()
    assert "## Diagram" in readme_text, (
        "package README must gain a Diagram section after Stage 5 runs"
    )
    assert "```mermaid" in readme_text
    assert "classDiagram" in readme_text
    # The merger should have collected class names from both per-class docs
    # AND deduplicated the shared `Card` class to a single declaration.
    assert "class Card" in readme_text
    assert "class Gateway" in readme_text
    assert "class Charge" in readme_text
    assert readme_text.count("class Card") == 1, (
        "Card was declared in both Gateway.md and Charge.md diagrams; "
        "the package overview must dedupe to a single declaration"
    )
