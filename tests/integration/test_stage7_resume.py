"""Stage 7 within-stage resume: system-rollup checkpoint survives mid-stage crash.

Mirrors the resume-test pattern from test_stage3_resume.py /
test_stage4_resume.py. Stage 7 is a single-artifact stage (key:
"system:rollup"); the artifact_index entry lets a rerun skip the doer/
checker loop when inputs + outputs are unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s7_system_rollup import (
    ARCHITECTURE_FILENAME,
    SYSTEM_FILENAME,
)
from designdoc.stages.s7_system_rollup import (
    run as stage_system,
)
from designdoc.state import PipelineState


class _CountingFakeSDK:
    """Returns valid doer + checker responses; tallies every LLM call."""

    def __init__(self) -> None:
        self.call_count: int = 0

    async def query(self, *, prompt: str, options: dict) -> dict:
        self.call_count += 1
        system = options.get("system_prompt") or ""
        if "system design writer" in system:
            return {
                "text": (
                    "<<<SYSTEM_DESIGN>>>\n## Overview\nstub\n\n"
                    "<<<ARCHITECTURE>>>\n## Containers\n- cli\n"
                ),
                "usage": {"input_tokens": 30, "output_tokens": 60, "cost_usd": 0.003},
            }
        if "system-design accuracy reviewer" in system:
            return {
                "text": json.dumps({"status": "pass", "summary": "ok"}),
                "usage": {"input_tokens": 30, "output_tokens": 10, "cost_usd": 0.001},
            }
        raise AssertionError(f"unexpected system prompt: {system[:80]}")


def _seed_readmes(output: Path, pkgs: dict[str, str]) -> None:
    for pkg, body in pkgs.items():
        (output / "packages" / pkg).mkdir(parents=True, exist_ok=True)
        (output / "packages" / pkg / "README.md").write_text(body)


def _make_runner(sdk: _CountingFakeSDK) -> ClaudeSDKRunner:
    return ClaudeSDKRunner(budget=CostAccumulator(cap_usd=5.0), sdk=sdk)


@pytest.mark.anyio("asyncio")
async def test_stage7_checkpoints_system_rollup(tmp_path: Path) -> None:
    """After a successful run, artifact_index has a 'system:rollup' entry
    with a non-empty input_hash and path."""
    output = tmp_path / "design"
    _seed_readmes(output, {"payments": "## payments", "reporting": "## reporting"})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    sdk = _CountingFakeSDK()
    await stage_system(state=state, runner=_make_runner(sdk))

    assert "system:rollup" in state.artifact_index, (
        "artifact_index should have an entry for 'system:rollup'"
    )
    entry = state.artifact_index["system:rollup"]
    assert entry["input_hash"] != "", "input_hash must be non-empty"
    assert "path" in entry, "entry must include a path"


@pytest.mark.anyio("asyncio")
async def test_stage7_rerun_skips_when_checkpointed(tmp_path: Path) -> None:
    """Two-run test: first writes artifact_index + outputs, second run with
    identical package READMEs makes ZERO LLM calls."""
    output = tmp_path / "design"
    _seed_readmes(output, {"payments": "## p", "reporting": "## r"})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    sdk1 = _CountingFakeSDK()
    await stage_system(state=state, runner=_make_runner(sdk1))
    assert sdk1.call_count > 0, "first run must make LLM calls"

    # Simulate a crash-resume by reloading state from disk.
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    sdk2 = _CountingFakeSDK()
    await stage_system(state=state2, runner=_make_runner(sdk2))
    assert sdk2.call_count == 0, (
        f"unchanged readmes made {sdk2.call_count} LLM calls on rerun; expected 0"
    )


@pytest.mark.anyio("asyncio")
async def test_stage7_mid_stage_crash_resume(tmp_path: Path) -> None:
    """Simulate a mid-stage crash: pre-populate artifact_index and the two
    rollup output files, then rerun. The doer/checker loop should not fire."""
    output = tmp_path / "design"
    _seed_readmes(output, {"payments": "## payments"})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    # Pre-seed outputs + artifact_index as if a prior run checkpointed but
    # crashed BEFORE marking the stage DONE.
    from designdoc.io_utils import sha1_keyed

    sys_path = output / SYSTEM_FILENAME
    arch_path = output / ARCHITECTURE_FILENAME
    sys_path.write_text("## Overview\npre-existing\n")
    arch_path.write_text("## Containers\n- cli\n")

    pkg_readmes = {"payments": "## payments"}
    input_hash = sha1_keyed(pkg_readmes)
    state.artifact_index["system:rollup"] = {
        "path": SYSTEM_FILENAME,
        "input_hash": input_hash,
    }
    state.rollup_hashes["system:rollup"] = input_hash
    state.save()

    sdk = _CountingFakeSDK()
    written = await stage_system(state=state, runner=_make_runner(sdk))

    assert sdk.call_count == 0, (
        f"pre-checkpointed rollup made {sdk.call_count} LLM calls on rerun; expected 0"
    )
    assert SYSTEM_FILENAME in written
    assert ARCHITECTURE_FILENAME in written
    # Existing outputs must be preserved, not overwritten.
    assert sys_path.read_text() == "## Overview\npre-existing\n"
    assert arch_path.read_text() == "## Containers\n- cli\n"


@pytest.mark.anyio("asyncio")
async def test_stage7_checkpoint_missing_output_file_regenerates(
    tmp_path: Path,
) -> None:
    """If artifact_index has an entry but the output file was manually
    deleted, the stage must regenerate rather than silently skip.

    Mirrors stage 3/4 semantics: skip gate requires BOTH matching input_hash
    AND the on-disk output to exist."""
    output = tmp_path / "design"
    _seed_readmes(output, {"payments": "## payments"})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    # artifact_index says "already produced" but SYSTEM_DESIGN.md is absent.
    from designdoc.io_utils import sha1_keyed

    pkg_readmes = {"payments": "## payments"}
    input_hash = sha1_keyed(pkg_readmes)
    state.artifact_index["system:rollup"] = {
        "path": SYSTEM_FILENAME,
        "input_hash": input_hash,
    }
    state.save()

    sdk = _CountingFakeSDK()
    await stage_system(state=state, runner=_make_runner(sdk))

    # Missing outputs on disk -> must re-run the loop.
    assert sdk.call_count > 0, (
        "missing output file must force regeneration despite artifact_index entry"
    )
    assert (output / SYSTEM_FILENAME).exists()
    assert (output / ARCHITECTURE_FILENAME).exists()
