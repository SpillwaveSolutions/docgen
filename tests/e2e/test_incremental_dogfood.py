"""Measurement e2e: cold run + warm run to quantify incremental speedup.

Runs the full pipeline against tiny_repo twice:
1. Cold run — everything regenerates. Burns the full ~62 LLM invocations.
2. Warm run — nothing changed since cold finished. All stages should skip.

Asserts the warm run costs less than 10% of the cold run and makes ~0
invocations. If this test ever regresses, the incremental-regeneration
claim is broken.

Gated by requires_api and the presence of the `claude` CLI + npx mmdc.
Expected runtime on a quiet machine: ~25 minutes (cold) + ~10 seconds
(warm). Expected cost: ~$4 for cold, ~$0 for warm.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from designdoc.budget import BUDGET_FILENAME, CostAccumulator
from designdoc.orchestrator import Orchestrator
from designdoc.runner import ClaudeSDKRunner
from designdoc.state import PipelineState

TINY_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_repo"

pytestmark = [
    pytest.mark.requires_api,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="`claude` CLI not on PATH — install Claude Code and log in",
    ),
    pytest.mark.skipif(
        shutil.which("npx") is None,
        reason="npx not available",
    ),
]


@pytest.mark.anyio
async def test_warm_run_skips_almost_everything(tmp_path: Path):
    output = tmp_path / "design"
    budget_path = output / BUDGET_FILENAME

    # --- Cold run ---
    cold_start = time.monotonic()
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)
    budget = CostAccumulator(cap_usd=5.00, path=budget_path)
    runner = ClaudeSDKRunner(budget=budget)
    await Orchestrator(state=state, runner=runner, budget=budget).run()
    cold_wall = time.monotonic() - cold_start
    cold_data = json.loads(budget_path.read_text())
    cold_invocations = cold_data["invocations"]
    cold_cost = cold_data["total_cost_usd"]
    print(f"\n[COLD] wall={cold_wall:.1f}s  cost=${cold_cost:.4f}  invocations={cold_invocations}")

    # --- Warm run (no source changes) ---
    warm_start = time.monotonic()
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)
    budget2 = CostAccumulator.load_or_new(cap_usd=10.00, path=budget_path)
    runner2 = ClaudeSDKRunner(budget=budget2)
    baseline_invocations = budget2.invocations
    baseline_cost = budget2.total_cost_usd
    await Orchestrator(state=state2, runner=runner2, budget=budget2).run()
    warm_wall = time.monotonic() - warm_start
    warm_data = json.loads(budget_path.read_text())
    warm_invocations = warm_data["invocations"] - baseline_invocations
    warm_cost = warm_data["total_cost_usd"] - baseline_cost
    print(
        f"[WARM] wall={warm_wall:.1f}s  delta_cost=${warm_cost:.4f}  "
        f"delta_invocations={warm_invocations}"
    )
    print(
        f"[SPEEDUP] wall {cold_wall / max(warm_wall, 0.001):.1f}×, "
        f"cost {cold_cost / max(warm_cost, 0.0001):.1f}×"
    )

    # Assertions — conservative bounds that catch regressions without being
    # flaky if the SDK's cost reporting shifts by cents.
    assert warm_invocations <= 2, f"warm run made {warm_invocations} LLM calls; expected ~0"
    assert warm_cost < cold_cost * 0.10, (
        f"warm cost ${warm_cost:.4f} is not <10% of cold ${cold_cost:.4f}"
    )
    # Wall-clock speedup: warm should be at least 10x faster (typically much more).
    assert warm_wall < cold_wall / 10.0, (
        f"warm wall {warm_wall:.1f}s is not <10% of cold {cold_wall:.1f}s"
    )
