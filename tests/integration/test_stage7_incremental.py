"""Stage 7 incremental: skip system + architecture rollup when no package
README changed. Skip key: "system:rollup" in state.rollup_hashes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s7_system_rollup import run as stage_system
from designdoc.state import PipelineState


class CountingFakeSDK:
    def __init__(self):
        self.calls: list[str] = []

    async def query(self, *, prompt, options):
        system = options.get("system_prompt") or ""
        self.calls.append(system[:40])
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
        raise AssertionError(f"unexpected agent: {system[:80]}")


def _seed_readmes(output: Path, pkgs: dict[str, str]) -> None:
    for pkg, body in pkgs.items():
        (output / "packages" / pkg).mkdir(parents=True, exist_ok=True)
        (output / "packages" / pkg / "README.md").write_text(body)


async def _run_stage7(state, fake):
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=5.0), sdk=fake)
    return await stage_system(state=state, runner=runner)


@pytest.mark.anyio
async def test_second_run_with_unchanged_readmes_skips(tmp_path: Path):
    output = tmp_path / "design"
    _seed_readmes(output, {"payments": "## p", "reporting": "## r"})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    fake1 = CountingFakeSDK()
    await _run_stage7(state, fake1)
    assert len(fake1.calls) > 0

    fake2 = CountingFakeSDK()
    await _run_stage7(state, fake2)
    assert fake2.calls == [], "unchanged package READMEs should skip rollup"


@pytest.mark.anyio
async def test_changed_readme_triggers_rollup(tmp_path: Path):
    output = tmp_path / "design"
    _seed_readmes(output, {"payments": "## p"})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    await _run_stage7(state, CountingFakeSDK())

    _seed_readmes(output, {"payments": "## p MODIFIED"})

    fake = CountingFakeSDK()
    await _run_stage7(state, fake)
    assert len(fake.calls) == 2, "changed package README -> rollup regenerates"


@pytest.mark.anyio
async def test_new_package_triggers_rollup(tmp_path: Path):
    output = tmp_path / "design"
    _seed_readmes(output, {"payments": "## p"})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    await _run_stage7(state, CountingFakeSDK())

    _seed_readmes(output, {"reporting": "## r"})

    fake = CountingFakeSDK()
    await _run_stage7(state, fake)
    assert len(fake.calls) == 2
