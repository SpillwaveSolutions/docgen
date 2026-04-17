"""Stage 4 incremental: skip package rollup when no class doc in that
package changed since the last successful run.

Skip key: "package:<pkg_name>" in state.rollup_hashes.
Skip value: SHA1 of the concatenation of all class-doc contents, in
filename order.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s4_package_rollups import run as stage_rollups
from designdoc.state import PipelineState


class CountingFakeSDK:
    def __init__(self):
        self.calls: list[str] = []

    async def query(self, *, prompt, options):
        system = options.get("system_prompt") or ""
        self.calls.append(system[:40])
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
        raise AssertionError(f"unexpected agent: {system[:80]}")


def _seed_packages(output_dir: Path, pkgs: dict[str, list[tuple[str, str]]]) -> None:
    for pkg, classes in pkgs.items():
        base = output_dir / "packages" / pkg / "classes"
        base.mkdir(parents=True, exist_ok=True)
        for cls, body in classes:
            (base / f"{cls}.md").write_text(body)


async def _run_stage4(state, fake):
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=5.0), sdk=fake)
    return await stage_rollups(state=state, runner=runner)


@pytest.mark.anyio
async def test_second_run_with_unchanged_classes_skips_rollup(tmp_path: Path):
    output = tmp_path / "design"
    _seed_packages(
        output,
        {
            "payments": [("Gateway", "## g1"), ("Charge", "## c1")],
            "reporting": [("Report", "## r1")],
        },
    )
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    fake1 = CountingFakeSDK()
    await _run_stage4(state, fake1)
    first_calls = len(fake1.calls)
    assert first_calls > 0

    # Second run: no class docs changed -> zero calls
    fake2 = CountingFakeSDK()
    await _run_stage4(state, fake2)
    assert fake2.calls == [], f"unchanged packages made {len(fake2.calls)} LLM calls; should be 0"


@pytest.mark.anyio
async def test_only_changed_package_regenerates(tmp_path: Path):
    output = tmp_path / "design"
    _seed_packages(
        output,
        {"payments": [("Gateway", "## g1")], "reporting": [("Report", "## r1")]},
    )
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    fake1 = CountingFakeSDK()
    await _run_stage4(state, fake1)

    # Modify only the payments class doc
    (output / "packages" / "payments" / "classes" / "Gateway.md").write_text("## g1 modified")

    fake2 = CountingFakeSDK()
    await _run_stage4(state, fake2)
    # Exactly one package regenerated: 2 calls (doer + checker)
    assert len(fake2.calls) == 2, (
        f"expected 2 LLM calls for one changed package, got {len(fake2.calls)}"
    )


@pytest.mark.anyio
async def test_new_package_regenerates_without_touching_unchanged_ones(tmp_path: Path):
    output = tmp_path / "design"
    _seed_packages(output, {"payments": [("Gateway", "## g1")]})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    await _run_stage4(state, CountingFakeSDK())

    # Add a whole new package
    _seed_packages(output, {"reporting": [("Report", "## r1")]})

    fake = CountingFakeSDK()
    await _run_stage4(state, fake)
    assert len(fake.calls) == 2, "only the new package should regenerate"

    # Both READMEs exist
    assert (output / "packages" / "payments" / "README.md").exists()
    assert (output / "packages" / "reporting" / "README.md").exists()
