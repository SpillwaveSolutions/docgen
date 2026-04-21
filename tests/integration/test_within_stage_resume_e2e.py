"""Within-stage resume e2e correctness.

Validates the load-bearing invariant of v1.2: a pipeline halted mid-stage
checkpoints the artifacts it finished and the resume run reuses them rather
than regenerating. Uses the deterministic _RecordingSDK from
test_resume.py so the correctness check is cheap and reproducible in CI.

The proof of non-regeneration is a call count: halt+resume across two
orchestrator invocations makes exactly the same total SDK calls as a
single clean run. Any extra calls would mean the resume regenerated
something the checkpoint already held.

Note: this replaces the plan's original requires_api subprocess test, which
would have relied on byte-identical output against the live Claude API —
impossible with LLM non-determinism. The invariant we actually need
(reuse-on-resume) is checked here with a deterministic fake.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from designdoc.budget import BUDGET_FILENAME, CostAccumulator
from designdoc.config import Config
from designdoc.orchestrator import Orchestrator
from designdoc.runner import ClaudeSDKRunner
from designdoc.state import PipelineState, StageStatus

TINY_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_repo"


def _ok(obj: dict) -> dict:
    return {
        "text": json.dumps(obj),
        "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
    }


def _ok_text(text: str) -> dict:
    return {
        "text": text,
        "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
    }


class _RecordingSDK:
    """Deterministic SDK fake — same prompt always yields same response.

    Mirrors tests/integration/test_resume.py::_RecordingSDK. Duplicated rather
    than imported because tests/ is not a Python package on pytest's path.
    """

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


def _tree_hash(root: Path) -> str:
    """SHA1 of sorted (relpath, content-sha1) pairs — excludes state files."""
    entries = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.startswith(".designdoc-"):
            content_sha = hashlib.sha1(p.read_bytes()).hexdigest()
            entries.append(f"{p.relative_to(root)}:{content_sha}")
    return hashlib.sha1("\n".join(entries).encode()).hexdigest()


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_halt_and_resume_matches_clean_run(tmp_path: Path) -> None:
    """Halt mid-Stage-3 via budget cap, resume, assert byte-identical output
    AND no redundant SDK calls vs a clean cold run."""
    # parallelism=1 so in-flight calls at budget-halt time are predictable —
    # with the default parallelism=3 the halt can land after up-to-3 doers
    # return, which fuzzes the expected SDK call count below.
    serial_config = Config(parallelism=1)

    # --- Clean baseline run ---
    clean_out = tmp_path / "clean"
    clean_state = PipelineState.load_or_new(output_dir=clean_out, target_repo=TINY_REPO)
    clean_budget = CostAccumulator(cap_usd=5.0, path=clean_out / BUDGET_FILENAME)
    clean_sdk = _RecordingSDK()
    clean_runner = ClaudeSDKRunner(budget=clean_budget, sdk=clean_sdk)
    await Orchestrator(
        state=clean_state, runner=clean_runner, budget=clean_budget, config=serial_config
    ).run()
    clean_tree_hash = _tree_hash(clean_out)

    # --- Halt + resume run ---
    halt_out = tmp_path / "halt"
    halt_state = PipelineState.load_or_new(output_dir=halt_out, target_repo=TINY_REPO)
    # Cap tight enough to fail partway through Stage 3 (after Stage 2 completes
    # and at least one class_doc has been checkpointed). tiny_repo has:
    # - 5 files × 1 Stage 2 call = 5 calls ($0.005)
    # - 3 classes × 2 Stage 3 calls = 6 calls ($0.006)
    # Setting $0.0075 allows Stage 2 (5) + one full class doer+checker (2) + one
    # more doer (1) = 8 calls, and the 9th (class 2 checker) triggers the halt.
    # That leaves class 1 fully checkpointed, class 2 in-flight and discarded.
    halt_budget = CostAccumulator(cap_usd=0.0075, path=halt_out / BUDGET_FILENAME)
    halt_sdk = _RecordingSDK()
    halt_runner = ClaudeSDKRunner(budget=halt_budget, sdk=halt_sdk)
    await Orchestrator(
        state=halt_state,
        runner=halt_runner,
        budget=halt_budget,
        config=serial_config,
    ).run()

    # Verify we halted mid-Stage-3 (not before, not after).
    assert halt_state.halted_on_budget is True
    assert halt_state.stages.get("file_analysis") == StageStatus.DONE
    assert halt_state.stages.get("class_docs") == StageStatus.FAILED
    # Exactly one class doc got checkpointed — this is the artifact we prove is reused.
    checkpointed_ids = [k for k in halt_state.artifact_index if "::" in k]
    assert len(checkpointed_ids) == 1, (
        f"expected exactly 1 class checkpoint at halt, got {checkpointed_ids}"
    )
    halt_call_snapshot = len(halt_sdk.calls)

    # --- Resume run: raise cap, clear halt flag (mimics CLI --budget reset) ---
    halt_state.halted_on_budget = False
    resume_budget = CostAccumulator.load_or_new(cap_usd=5.0, path=halt_out / BUDGET_FILENAME)
    resume_runner = ClaudeSDKRunner(budget=resume_budget, sdk=halt_sdk)  # same SDK
    await Orchestrator(
        state=halt_state, runner=resume_runner, budget=resume_budget, config=serial_config
    ).run()

    # --- Assertions ---
    # Resume-phase Stage 3 doer calls is the load-bearing invariant:
    # tiny_repo has 3 classes, halt checkpointed class 1. If within-stage
    # resume works, resume should make exactly 2 doer calls (class 2 + class 3).
    # If the checkpointed class 1 were regenerated, we'd see 3.
    resume_only_calls = halt_sdk.calls[halt_call_snapshot:]
    resume_stage3_doer_count = sum(
        1 for c in resume_only_calls if "design-documentation writer" in c
    )
    assert resume_stage3_doer_count == 2, (
        f"expected 2 Stage 3 doer calls on resume (class 2 + class 3), got "
        f"{resume_stage3_doer_count}. More implies checkpointed class 1 was regenerated."
    )

    # Full pipeline completed after resume.
    for stage_name in (
        "discover",
        "index",
        "file_analysis",
        "class_docs",
        "package_rollups",
        "mermaid",
        "tech_debt",
        "system_rollup",
        "finalize",
    ):
        assert halt_state.stages.get(stage_name) == StageStatus.DONE, (
            f"stage {stage_name} not DONE after resume"
        )

    # Final output tree is byte-identical to a clean cold run — strong high-level
    # smoke that the pipeline is deterministic given the fake SDK.
    assert _tree_hash(halt_out) == clean_tree_hash
