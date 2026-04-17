"""Stage 3 incremental behavior.

When a source file's SHA1 is unchanged since the last run AND its class doc
already exists on disk, skip the doer/checker loop entirely for each class
in that file.
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
from designdoc.state import PipelineState

TINY_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_repo"


class CountingFakeSDK:
    def __init__(self):
        self.calls: list[str] = []

    async def query(self, *, prompt, options):
        system = options.get("system_prompt") or ""
        self.calls.append(system[:40])
        if "design-documentation writer" in system:
            return {
                "text": "## Purpose\nStub class doc.\n",
                "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
            }
        if "documentation QA reviewer" in system:
            return {
                "text": json.dumps({"status": "pass", "summary": "ok"}),
                "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0005},
            }
        raise AssertionError(f"unexpected agent: {system[:60]}")


async def _run_stage3(state, fake):
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=5.0), sdk=fake)
    return await stage_class_docs(state=state, runner=runner)


@pytest.mark.anyio
async def test_unchanged_file_skips_class_docs_on_second_run(tmp_path: Path):
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    # First run: everything regenerates.
    state.prev_hashes = json.loads((output / "stage0_discovery.json").read_text())["hashes"]
    state.save()
    fake1 = CountingFakeSDK()
    first = await _run_stage3(state, fake1)
    first_calls = len(fake1.calls)
    assert first_calls > 0

    # Second run with no source changes: class docs already on disk and all
    # hashes match — Stage 3 must make zero LLM calls.
    fake2 = CountingFakeSDK()
    second = await _run_stage3(state, fake2)
    assert fake2.calls == [], f"unchanged second run made {len(fake2.calls)} LLM calls; should be 0"
    # The returned dict still lists every class so downstream stages see them
    assert set(second.keys()) == set(first.keys())


@pytest.mark.anyio
async def test_changed_source_regenerates_only_its_classes(tmp_path: Path):
    """Modify one source file -> only its classes regenerate; others skip."""
    # Use tmp_path as a copy of tiny_repo so we can mutate it
    import shutil

    repo = tmp_path / "repo"
    shutil.copytree(TINY_REPO, repo)
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=repo)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)
    state.prev_hashes = json.loads((output / "stage0_discovery.json").read_text())["hashes"]
    state.save()
    await _run_stage3(state, CountingFakeSDK())

    # Modify gateway.py (contains StripeGateway + Charge dataclass)
    gw = repo / "src" / "tiny" / "payments" / "gateway.py"
    gw.write_text(gw.read_text() + "\n# extra comment to change hash\n")

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    fake = CountingFakeSDK()
    await _run_stage3(state, fake)
    # gateway.py has 2 classes (Charge dataclass + StripeGateway). Each class
    # triggers 1 doer + 1 checker = 2 calls. So 2 classes * 2 roles = 4 calls.
    # report.py's Report class should NOT have regenerated.
    # Exact count depends on ast output but it must be 0 < calls < first-run-total.
    assert 0 < len(fake.calls) < 6, f"expected partial regeneration, got {len(fake.calls)} calls"
