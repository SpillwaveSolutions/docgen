"""Stage 4 within-stage resume: per-package checkpoint survives mid-stage crash."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages import s4_package_rollups
from designdoc.state import PipelineState


class _CountingFakeSDK:
    """Mirrors CountingFakeSDK from test_stage4_incremental; counts LLM calls."""

    def __init__(self) -> None:
        self.call_count: int = 0

    async def query(self, *, prompt: str, options: dict) -> dict:
        self.call_count += 1
        system = options.get("system_prompt") or ""
        if "package-level documentation writer" in system:
            return {
                "text": "## Overview\nStub package rollup.\n",
                "usage": {"input_tokens": 20, "output_tokens": 40, "cost_usd": 0.002},
            }
        if "rollup-accuracy reviewer" in system:
            return {
                "text": json.dumps({"status": "pass", "summary": "ok"}),
                "usage": {"input_tokens": 20, "output_tokens": 10, "cost_usd": 0.001},
            }
        raise AssertionError(f"unexpected system prompt: {system[:80]}")


def _seed_class_docs(output: Path, pkg: str, classes: list[str]) -> None:
    pkg_dir = output / "packages" / pkg / "classes"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    for c in classes:
        (pkg_dir / f"{c}.md").write_text(f"# {c}\n\nclass doc")


def _make_runner(sdk: _CountingFakeSDK) -> ClaudeSDKRunner:
    return ClaudeSDKRunner(budget=CostAccumulator(cap_usd=10.0), sdk=sdk)


@pytest.mark.anyio("asyncio")
async def test_stage4_checkpoints_per_package(tmp_path: Path) -> None:
    """After a successful run, artifact_index has a 'package:<pkg>' entry
    with a non-empty input_hash."""
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_class_docs(output, "alpha", ["A1", "A2"])

    sdk = _CountingFakeSDK()
    await s4_package_rollups.run(state=state, runner=_make_runner(sdk))

    assert "package:alpha" in state.artifact_index, (
        "artifact_index should have an entry for 'package:alpha'"
    )
    entry = state.artifact_index["package:alpha"]
    assert entry["input_hash"] != "", "input_hash must be non-empty"
    assert "path" in entry, "entry must include a path"


@pytest.mark.anyio("asyncio")
async def test_stage4_rerun_skips_checkpointed_packages(tmp_path: Path) -> None:
    """Two-run test: first produces package README + artifact_index entry;
    second run with identical class-doc hashes should make ZERO LLM calls."""
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_class_docs(output, "alpha", ["A1", "A2"])

    # First run: LLM is called.
    sdk1 = _CountingFakeSDK()
    await s4_package_rollups.run(state=state, runner=_make_runner(sdk1))
    first_call_count = sdk1.call_count
    assert first_call_count > 0, "first run must make LLM calls"

    # Reload state from disk (simulating a crash-resume).
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    # Second run: class docs unchanged -> zero LLM calls.
    sdk2 = _CountingFakeSDK()
    await s4_package_rollups.run(state=state2, runner=_make_runner(sdk2))
    assert sdk2.call_count == 0, (
        f"unchanged packages made {sdk2.call_count} LLM calls on second run; expected 0"
    )


@pytest.mark.anyio("asyncio")
async def test_stage4_partial_crash_resume(tmp_path: Path) -> None:
    """Simulate a mid-stage crash: pre-populate artifact_index for one package,
    verify that only the other package is processed on the next run."""
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_class_docs(output, "alpha", ["A1"])
    _seed_class_docs(output, "beta", ["B1"])

    # Simulate alpha having been completed before crash:
    # write its README and artifact_index + rollup_hashes entries manually.
    alpha_readme = output / "packages" / "alpha" / "README.md"
    alpha_readme.write_text("## Alpha\nPre-existing readme.\n")
    # Compute what the hash would be so skip gate fires.
    import hashlib

    h = hashlib.sha1()
    alpha_doc = "# A1\n\nclass doc"
    h.update(b"A1\x00")
    h.update(alpha_doc.encode("utf-8"))
    h.update(b"\n")
    alpha_hash = h.hexdigest()

    state.artifact_index["package:alpha"] = {
        "path": "packages/alpha/README.md",
        "input_hash": alpha_hash,
    }
    state.rollup_hashes["package:alpha"] = alpha_hash
    state.save()

    # Now run: only beta should be processed.
    sdk = _CountingFakeSDK()
    written = await s4_package_rollups.run(state=state, runner=_make_runner(sdk))

    assert "beta" in written, "beta package should have been processed"
    assert "alpha" in written, "alpha package should appear in written (skipped)"
    # Doer + checker = 2 calls for beta only.
    assert sdk.call_count == 2, f"expected 2 LLM calls for beta only, got {sdk.call_count}"
    assert "package:beta" in state.artifact_index
