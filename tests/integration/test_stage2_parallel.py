"""Stage 2 parallel execution.

Behavior under parallelism=3 must match parallelism=1. Concurrency is
observed via a FakeSDK that records in-flight count at each call and
asserts the peak exceeded 1.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s0_discover import run as stage_discover
from designdoc.stages.s1_index import run as stage_index
from designdoc.stages.s2_file_analysis import OUTPUT_FILENAME
from designdoc.stages.s2_file_analysis import run as stage_file_analysis
from designdoc.state import PipelineState


class ConcurrencyRecordingSDK:
    """Records peak in-flight count across all query() calls."""

    def __init__(self, delay: float = 0.02):
        self.in_flight = 0
        self.peak = 0
        self.delay = delay
        self.total_calls = 0

    async def query(self, *, prompt, options):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.total_calls += 1
        try:
            await asyncio.sleep(self.delay)
            return {
                "text": json.dumps(
                    {
                        "purpose": f"stub #{self.total_calls}",
                        "key_types": [],
                        "key_functions": [],
                        "external_deps": [],
                        "notes": "",
                    }
                ),
                "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
            }
        finally:
            self.in_flight -= 1


def _seed_repo(tmp_path: Path, n: int) -> None:
    for i in range(n):
        (tmp_path / f"f{i}.py").write_text(f"# file {i}\nv = {i}\n")


@pytest.mark.anyio
async def test_parallelism_3_processes_all_files(tmp_path: Path):
    """Correctness: every file still gets a summary under parallelism=3."""
    _seed_repo(tmp_path, 6)
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    sdk = ConcurrencyRecordingSDK(delay=0.0)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=5.0), sdk=sdk)
    summaries = await stage_file_analysis(state=state, runner=runner, parallelism=3)

    assert len(summaries) == 6
    data = json.loads((output / OUTPUT_FILENAME).read_text())
    assert len(data) == 6


@pytest.mark.anyio
async def test_parallelism_3_actually_runs_concurrently(tmp_path: Path):
    """Observability: multiple doer calls are in-flight at once."""
    _seed_repo(tmp_path, 6)
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    sdk = ConcurrencyRecordingSDK(delay=0.05)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=5.0), sdk=sdk)
    await stage_file_analysis(state=state, runner=runner, parallelism=3)

    # At least 2 calls must have overlapped at some point
    assert sdk.peak >= 2, f"no concurrency observed; peak in-flight was {sdk.peak}"
    # But never more than 3 (the semaphore cap)
    assert sdk.peak <= 3, f"exceeded parallelism cap; peak was {sdk.peak}"


@pytest.mark.anyio
async def test_parallelism_1_is_serial(tmp_path: Path):
    """parallelism=1 means no concurrency — peak in-flight stays at 1."""
    _seed_repo(tmp_path, 4)
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    sdk = ConcurrencyRecordingSDK(delay=0.01)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=5.0), sdk=sdk)
    await stage_file_analysis(state=state, runner=runner, parallelism=1)

    assert sdk.peak == 1, f"parallelism=1 should be serial; peak was {sdk.peak}"
