"""Stage 2 within-stage resume: mid-stage raise + rerun = no duplicate LLM calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages import s1_index, s2_file_analysis
from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.state import PipelineState


def _seed_stage0_and_stage1(state: PipelineState, files: dict[str, str]) -> None:
    """Write stage0_discovery.json (hashes) and stage1 signatures.json."""
    hashes = {p: hashlib.sha1(c.encode()).hexdigest() for p, c in files.items()}
    (state.output_dir / STAGE0_FILENAME).write_text(
        json.dumps({"hashes": hashes, "languages": {"python": list(files)}})
    )
    sigs = [
        {"path": p, "classes": [], "functions": [], "imports": [], "parse_error": None}
        for p in files
    ]
    (state.output_dir / s1_index.OUTPUT_FILENAME).write_text(json.dumps(sigs))


def _valid_summary_json(n: int = 0) -> str:
    return json.dumps(
        {
            "purpose": f"stub #{n}",
            "key_types": [],
            "key_functions": [],
            "external_deps": [],
            "notes": "",
        }
    )


class _CountingFakeSDK:
    """FakeSDK that counts calls and returns a valid FileSummary JSON."""

    def __init__(self) -> None:
        self.call_count: int = 0

    async def query(self, *, prompt: str, options: dict) -> dict:
        self.call_count += 1
        return {
            "text": _valid_summary_json(self.call_count),
            "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.0},
        }


class _CrashAfterOneFakeSDK:
    """FakeSDK that succeeds once then raises RuntimeError to simulate a crash."""

    def __init__(self) -> None:
        self.call_count: int = 0

    async def query(self, *, prompt: str, options: dict) -> dict:
        self.call_count += 1
        if self.call_count == 2:
            raise RuntimeError("simulated mid-stage crash")
        return {
            "text": _valid_summary_json(self.call_count),
            "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.0},
        }


def _make_runner(sdk) -> ClaudeSDKRunner:
    return ClaudeSDKRunner(budget=CostAccumulator(cap_usd=10.0), sdk=sdk)


@pytest.mark.anyio("asyncio")
async def test_stage2_checkpoints_after_each_file(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_stage0_and_stage1(state, {"a.py": "print(1)", "b.py": "print(2)"})

    sdk = _CountingFakeSDK()
    await s2_file_analysis.run(state=state, runner=_make_runner(sdk), parallelism=1)

    # Both files have artifact_index entries, each with a non-empty input_hash.
    assert "file:a.py" in state.artifact_index
    assert "file:b.py" in state.artifact_index
    assert state.artifact_index["file:a.py"]["input_hash"] != ""


@pytest.mark.anyio("asyncio")
async def test_stage2_rerun_skips_checkpointed_files(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_stage0_and_stage1(state, {"a.py": "print(1)", "b.py": "print(2)"})

    # First run: both files processed.
    sdk1 = _CountingFakeSDK()
    await s2_file_analysis.run(state=state, runner=_make_runner(sdk1), parallelism=1)
    assert sdk1.call_count == 2

    # Reload state (simulates process death + rerun).
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    sdk2 = _CountingFakeSDK()
    await s2_file_analysis.run(state=state2, runner=_make_runner(sdk2), parallelism=1)
    # No LLM calls on the rerun — everything already checkpointed.
    assert sdk2.call_count == 0


@pytest.mark.anyio("asyncio")
async def test_stage2_mid_stage_crash_resumes_cleanly(tmp_path: Path) -> None:
    """Simulate SIGKILL after 1 of 2 files: re-run processes ONLY the remaining file."""
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_stage0_and_stage1(state, {"a.py": "print(1)", "b.py": "print(2)"})

    crash_sdk = _CrashAfterOneFakeSDK()
    with pytest.raises(RuntimeError, match="simulated mid-stage crash"):
        await s2_file_analysis.run(state=state, runner=_make_runner(crash_sdk), parallelism=1)

    # One file got checkpointed before the crash.
    assert len(state.artifact_index) == 1

    # Reload + re-run with a clean SDK: only the un-checkpointed file fires.
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    clean_sdk = _CountingFakeSDK()
    await s2_file_analysis.run(state=state2, runner=_make_runner(clean_sdk), parallelism=1)
    assert clean_sdk.call_count == 1
