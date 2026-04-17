"""Integration: Stage 7 with pre-populated packages/*/README.md files."""

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
from designdoc.state import PipelineState, StageStatus


class FakeSDK:
    async def query(self, *, prompt: str, options: dict) -> dict:
        system = options.get("system_prompt") or ""
        if "system design writer" in system:
            return {
                "text": (
                    "<<<SYSTEM_DESIGN>>>\n"
                    "## Overview\nStub system overview.\n\n"
                    "## Packages\n- payments: stub\n- reporting: stub\n\n"
                    "<<<ARCHITECTURE>>>\n"
                    "## Containers\n- cli\n\n## Components\n- payments.Gateway\n"
                ),
                "usage": {"input_tokens": 30, "output_tokens": 60, "cost_usd": 0.003},
            }
        if "system-design accuracy reviewer" in system:
            return {
                "text": json.dumps({"status": "pass", "summary": "ok"}),
                "usage": {"input_tokens": 30, "output_tokens": 10, "cost_usd": 0.001},
            }
        raise AssertionError(f"unexpected system prompt: {system[:80]}")


@pytest.mark.anyio
async def test_stage7_writes_both_rollup_files(tmp_path: Path):
    output = tmp_path / "design"
    pkgs = output / "packages"
    (pkgs / "payments").mkdir(parents=True)
    (pkgs / "reporting").mkdir(parents=True)
    (pkgs / "payments" / "README.md").write_text("## Overview\npayments")
    (pkgs / "reporting" / "README.md").write_text("## Overview\nreporting")

    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())

    written = await stage_system(state=state, runner=runner)

    assert state.stages["system_rollup"] == StageStatus.DONE
    assert SYSTEM_FILENAME in written
    assert ARCHITECTURE_FILENAME in written
    sys_md = (output / SYSTEM_FILENAME).read_text()
    arch_md = (output / ARCHITECTURE_FILENAME).read_text()
    assert "## Overview" in sys_md
    assert "## Containers" in arch_md


@pytest.mark.anyio
async def test_stage7_requires_package_readmes(tmp_path: Path):
    output = tmp_path / "design"
    (output / "packages" / "empty").mkdir(parents=True)
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())
    with pytest.raises(FileNotFoundError):
        await stage_system(state=state, runner=runner)
