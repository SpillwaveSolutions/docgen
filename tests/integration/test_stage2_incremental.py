"""Stage 2 incremental behavior (v1.1 step 2).

Previously Stage 2 invoked the file-analyzer doer for every indexed file on
every run. With prev_hashes wired up, files whose SHA1 hasn't changed since
the last successful run reuse the previously persisted summary — 0 LLM
invocations for those files. Changed and new files still regenerate.
"""

from __future__ import annotations

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


class CountingFakeSDK:
    """Returns a valid FileSummary JSON and records the number of calls."""

    def __init__(self):
        self.call_count = 0

    async def query(self, *, prompt, options):
        self.call_count += 1
        return {
            "text": json.dumps(
                {
                    "purpose": f"stub summary #{self.call_count}",
                    "key_types": [],
                    "key_functions": [],
                    "external_deps": [],
                    "notes": "",
                }
            ),
            "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
        }


async def _run_stage2(state: PipelineState, fake: CountingFakeSDK) -> dict:
    budget = CostAccumulator(cap_usd=5.0)
    runner = ClaudeSDKRunner(budget=budget, sdk=fake)
    return await stage_file_analysis(state=state, runner=runner)


def _seed_repo(tmp_path: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


@pytest.mark.anyio
async def test_unchanged_file_skips_llm_call(tmp_path: Path):
    """Second run with identical content -> zero invocations."""
    _seed_repo(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    # Seed prev_hashes so state.unchanged_paths() will match both files.
    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)
    state.prev_hashes = json.loads((output / "stage0_discovery.json").read_text())["hashes"]
    state.save()

    fake = CountingFakeSDK()
    await _run_stage2(state, fake)

    # Both files are unchanged — but we have no persisted stage2_summaries.json
    # from a "previous run", so they still regenerate. This validates that the
    # skip logic requires both conditions: hash unchanged AND previous summary
    # exists.
    assert fake.call_count == 2, "no previous summaries -> must regenerate"

    # Now simulate a "second run": prev_hashes match, and stage2 summaries
    # are already on disk from the first pass. The file-analyzer should
    # not be invoked again.
    fake2 = CountingFakeSDK()
    await _run_stage2(state, fake2)
    assert fake2.call_count == 0, "unchanged + previous summaries -> skip all"


@pytest.mark.anyio
async def test_changed_file_regenerates_only_that_file(tmp_path: Path):
    _seed_repo(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    # First run: populate prev_hashes + stage2 summaries
    state.prev_hashes = json.loads((output / "stage0_discovery.json").read_text())["hashes"]
    state.save()
    fake1 = CountingFakeSDK()
    await _run_stage2(state, fake1)
    assert fake1.call_count == 2

    # Modify a.py, re-discover, re-run stage 2
    (tmp_path / "a.py").write_text("x = 999  # changed\n")
    await stage_discover(state=state, exclude_paths=[])

    fake2 = CountingFakeSDK()
    await _run_stage2(state, fake2)
    # Only the changed file (a.py) should have triggered a new LLM call.
    assert fake2.call_count == 1, f"expected 1 regenerated file, got {fake2.call_count}"

    # Summaries file must still include both files.
    data = json.loads((output / OUTPUT_FILENAME).read_text())
    assert "a.py" in data
    assert "b.py" in data


@pytest.mark.anyio
async def test_new_file_regenerates(tmp_path: Path):
    _seed_repo(tmp_path, {"a.py": "x = 1\n"})
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)
    state.prev_hashes = json.loads((output / "stage0_discovery.json").read_text())["hashes"]
    state.save()
    fake1 = CountingFakeSDK()
    await _run_stage2(state, fake1)
    assert fake1.call_count == 1

    # Add a new file, re-discover, re-run
    (tmp_path / "c.py").write_text("z = 3\n")
    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    fake2 = CountingFakeSDK()
    await _run_stage2(state, fake2)
    assert fake2.call_count == 1, "only the new file should regenerate"

    data = json.loads((output / OUTPUT_FILENAME).read_text())
    assert "a.py" in data
    assert "c.py" in data


@pytest.mark.anyio
async def test_deleted_file_drops_from_summaries(tmp_path: Path):
    _seed_repo(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)
    state.prev_hashes = json.loads((output / "stage0_discovery.json").read_text())["hashes"]
    state.save()
    await _run_stage2(state, CountingFakeSDK())

    # Delete b.py, re-discover, re-run
    (tmp_path / "b.py").unlink()
    await stage_discover(state=state, exclude_paths=[])
    await stage_index(state=state)

    fake2 = CountingFakeSDK()
    await _run_stage2(state, fake2)
    assert fake2.call_count == 0, "a.py unchanged, b.py gone -> zero calls"

    data = json.loads((output / OUTPUT_FILENAME).read_text())
    assert "a.py" in data
    assert "b.py" not in data
