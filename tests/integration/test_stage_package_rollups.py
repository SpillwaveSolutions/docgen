"""Integration: Stage 4 against a pre-populated packages/ tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s4_package_rollups import run as stage_rollups
from designdoc.state import PipelineState, StageStatus


class FakeSDK:
    """Documenter returns markdown. Checker returns pass-JSON."""

    async def query(self, *, prompt: str, options: dict) -> dict:
        system = options.get("system_prompt") or ""
        if "package-level documentation writer" in system:
            return {
                "text": "## Overview\nStub package summary.\n\n## Classes\n- Foo: stub\n",
                "usage": {"input_tokens": 20, "output_tokens": 40, "cost_usd": 0.002},
            }
        if "rollup-accuracy reviewer" in system:
            return {
                "text": json.dumps({"status": "pass", "summary": "ok"}),
                "usage": {"input_tokens": 20, "output_tokens": 10, "cost_usd": 0.001},
            }
        raise AssertionError(f"unexpected system prompt: {system[:80]}")


def _seed_packages(output_dir: Path, pkgs: dict[str, list[str]]) -> None:
    """Create output_dir/packages/<pkg>/classes/<Class>.md for each class."""
    for pkg, classes in pkgs.items():
        base = output_dir / "packages" / pkg / "classes"
        base.mkdir(parents=True, exist_ok=True)
        for cls in classes:
            (base / f"{cls}.md").write_text(f"## Purpose\n{cls} stub doc.")


@pytest.mark.anyio
async def test_stage4_writes_readme_per_package(tmp_path: Path):
    output = tmp_path / "design"
    _seed_packages(output, {"payments": ["Gateway", "Charge"], "reporting": ["Report"]})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())
    written = await stage_rollups(state=state, runner=runner)

    assert state.stages["package_rollups"] == StageStatus.DONE
    assert set(written.keys()) == {"payments", "reporting"}
    for rel in written.values():
        assert (output / rel).exists()
        assert (output / rel).read_text().startswith("## ")


@pytest.mark.anyio
async def test_stage4_skips_empty_packages(tmp_path: Path):
    output = tmp_path / "design"
    (output / "packages" / "empty").mkdir(parents=True)
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())

    written = await stage_rollups(state=state, runner=runner)
    assert written == {}


@pytest.mark.anyio
async def test_stage4_requires_packages_dir(tmp_path: Path):
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())
    with pytest.raises(FileNotFoundError):
        await stage_rollups(state=state, runner=runner)
