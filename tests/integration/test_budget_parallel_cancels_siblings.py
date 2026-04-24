"""Budget leak under parallelism: siblings must cancel when one trips the cap.

Regression for issue #6. With asyncio.gather, a BudgetExceededError raised by
one in-flight task does NOT cancel siblings — they run to completion, burning
up to (parallelism - 1) extra paid LLM calls past the cap. TaskGroup fixes
this by cancelling siblings on first exception.

The test uses a FakeSDK that:
- counts every query() call,
- sleeps a bit so the loop actually holds sibling tasks in flight,
- returns a valid FileSummary JSON plus a cost that trips the cap on the 2nd
  accrual.

With the fix, the total SDK call count must be <= 2 (the 2nd call trips the
cap; TaskGroup cancels siblings before they start/complete their own queries).
With the old gather-based code, the count would be ``parallelism`` because
every sibling task already pulled a semaphore slot and awaited query() before
its accrual ran.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from designdoc.budget import BudgetExceededError, CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s0_discover import run as stage_discover
from designdoc.stages.s1_index import run as stage_index
from designdoc.stages.s2_file_analysis import run as stage_file_analysis
from designdoc.state import PipelineState


class CountingSDK:
    """FakeSDK that records every query() and can pause each call.

    We deliberately hold each call for `delay` seconds so the TaskGroup has
    real in-flight siblings when the first BudgetExceededError fires. Without
    the sleep, tasks might serialise fast enough to mask the leak.

    ``completed_calls`` counts queries that finished (paid) rather than
    started, so cancelled mid-flight tasks do NOT inflate the count.
    """

    def __init__(self, delay: float = 0.05):
        self.started_calls = 0
        self.completed_calls = 0
        self.delay = delay

    async def query(self, *, prompt, options):
        self.started_calls += 1
        # Each paid call costs enough that the 2nd accrual blows the $0.015 cap.
        await asyncio.sleep(self.delay)
        # A cancellation that arrives during the sleep propagates here and
        # we never reach the return — completed_calls only tracks finished queries.
        self.completed_calls += 1
        return {
            "text": json.dumps(
                {
                    "purpose": f"stub #{self.completed_calls}",
                    "key_types": [],
                    "key_functions": [],
                    "external_deps": [],
                    "notes": "",
                }
            ),
            "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.01},
        }


def _seed_repo(tmp_path: Path, n: int) -> None:
    for i in range(n):
        (tmp_path / f"f{i}.py").write_text(f"# file {i}\nv = {i}\n")


@pytest.mark.anyio
async def test_budget_cap_cancels_sibling_tasks_in_stage2(tmp_path: Path):
    """Stage 2 under parallelism=4: first cap-trip cancels siblings.

    With the leak (asyncio.gather): pending sibling tasks keep running after
    the accrual raises. With 6 files and parallelism=4, the old code would
    complete all 6 SDK queries even after the first BudgetExceededError.

    With the fix (asyncio.TaskGroup): the first raise cancels siblings before
    they complete their paid query. The total number of completed SDK calls
    should not exceed ``parallelism`` (tasks already past the await-point
    still finish, but pending-sem tasks are cancelled).
    """
    n_files = 20
    parallelism = 4
    _seed_repo(tmp_path, n_files)
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    sdk = CountingSDK(delay=0.05)
    # cap = 0.005; every call costs 0.01, so the FIRST accrual raises.
    budget = CostAccumulator(cap_usd=0.005)
    runner = ClaudeSDKRunner(budget=budget, sdk=sdk)

    with pytest.raises(BudgetExceededError):
        await stage_file_analysis(state=state, runner=runner, parallelism=parallelism)

    # Strict bound: once the cap trips, pending-sem siblings must NOT be
    # allowed to queue through the released semaphore slot and start a new
    # paid SDK call. With the asyncio.gather leak, EVERY file's sibling
    # task eventually gets scheduled past the sem release and fires its own
    # sdk.query() — so started_calls == n_files (the sibling-cancel window
    # is effectively unbounded, leaking (n_files - parallelism) extra paid
    # calls). With TaskGroup, the first exception cancels pending-sem
    # waiters, so started_calls stays close to `parallelism` — a small
    # amount of scheduling race may allow one extra task to enter query()
    # before cancellation lands, which is why we allow `parallelism + 1`.
    max_allowed = parallelism + 1
    assert sdk.started_calls <= max_allowed, (
        f"sibling tasks leaked past BudgetExceededError: "
        f"{sdk.started_calls} SDK queries started (expected <= {max_allowed}). "
        f"Pending-sem tasks grabbed the slot after the cap tripped. "
        f"This is the asyncio.gather leak — switch to TaskGroup."
    )
    # Sanity: we must stop well before every file ran (otherwise the cap
    # did nothing and the assertion above is meaningless).
    assert sdk.started_calls < n_files, (
        f"budget cap did not halt execution: all {n_files} files ran."
    )
