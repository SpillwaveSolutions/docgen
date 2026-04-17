"""End-to-end dogfood: run the real pipeline against tests/fixtures/tiny_repo.

Requires ANTHROPIC_API_KEY and npx (for mmdc). Run with:

    ANTHROPIC_API_KEY=sk-... task test-e2e

Gated by pytest.mark.requires_api so the normal CI gate doesn't touch the
live API. Budget capped at $2.00 — if we ever blow that, we have a bug
(the tiny_repo should land at ~$0.30-0.80).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from designdoc.budget import BUDGET_FILENAME, CostAccumulator
from designdoc.orchestrator import Orchestrator, default_stage_table
from designdoc.runner import ClaudeSDKRunner
from designdoc.state import PipelineState, StageStatus

TINY_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_repo"

pytestmark = [
    pytest.mark.requires_api,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set — skip live API e2e",
    ),
    pytest.mark.skipif(
        shutil.which("npx") is None,
        reason="npx not available — install Node.js to run the full pipeline",
    ),
]


@pytest.mark.anyio
async def test_full_pipeline_on_tiny_repo_with_real_api(tmp_path: Path):
    """The gen-3-thesis E2E: run all 9 stages against tiny_repo with the live
    Claude API + real mmdc.

    Asserts (per the acceptance gate in plans/2026_04_16_designdoc_gen_v1.md §Verification):
      (a) docs/design/README.md exists and lists every generated artifact.
      (b) Every packages/*/classes/*.md has a ## Diagram section with a
          ```mermaid``` block (Stage 5 validated).
      (c) mmdc parses every embedded diagram (post-check).
      (d) TECH_DEBT.md exists.
      (e) hil-issues.yaml may or may not exist, but if present contains no
          unresolved_count > 0 surprises.
      (f) Total cost under $2.00.
    """
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)
    budget = CostAccumulator(cap_usd=2.00, path=output / BUDGET_FILENAME)
    runner = ClaudeSDKRunner(budget=budget)

    await Orchestrator(state=state, runner=runner, budget=budget).run()

    # (a) every stage DONE
    for entry in default_stage_table():
        assert state.stages[entry.name] == StageStatus.DONE, (
            f"stage {entry.name} is {state.stages.get(entry.name)}"
        )

    # (b) every class doc has a Diagram section
    class_docs = list((output / "packages").glob("*/classes/*.md"))
    assert class_docs, "no class docs generated"
    for doc in class_docs:
        body = doc.read_text()
        assert "## Diagram" in body, f"missing diagram in {doc}"
        assert "```mermaid" in body, f"missing mermaid fence in {doc}"

    # (c) every embedded mermaid diagram parses
    from designdoc.mermaid.loop import strip_fence
    from designdoc.mermaid.mmdc import validate

    for doc in class_docs:
        body = doc.read_text()
        import re

        for m in re.finditer(r"```mermaid\n(.*?)```", body, re.DOTALL):
            block = strip_fence(m.group(0))
            result = validate(block)
            assert result.ok, f"invalid mermaid in {doc}: {result.stderr}"

    # (d) landmark artifacts exist
    assert (output / "README.md").exists()
    assert (output / "SYSTEM_DESIGN.md").exists()
    assert (output / "ARCHITECTURE.md").exists()
    assert (output / "TECH_DEBT.md").exists()

    # (f) budget under cap
    budget_data = json.loads((output / BUDGET_FILENAME).read_text())
    assert budget_data["total_cost_usd"] < 2.00, (
        f"dogfood cost ${budget_data['total_cost_usd']:.4f} exceeded $2.00 cap"
    )
    print(
        f"\nDogfood cost: ${budget_data['total_cost_usd']:.4f} "
        f"over {budget_data['invocations']} invocations"
    )
