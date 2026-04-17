"""Stage 6 incremental: skip tech-debt research when dependency manifests
are unchanged from the last successful run.

Skip key: "tech_debt" in state.rollup_hashes.
Skip value: SHA1 over (name, pinned, source) triples from parse_manifests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s6_tech_debt import run as stage_tech_debt
from designdoc.state import PipelineState


class CountingFakeSDK:
    def __init__(self):
        self.calls: list[str] = []

    async def query(self, *, prompt, options):
        system = options.get("system_prompt") or ""
        self.calls.append(system[:40])
        if "tech-debt researcher" in system:
            return {
                "text": json.dumps(
                    {
                        "name": "requests",
                        "pinned": ">=2.31",
                        "latest": "2.32",
                        "status": "current",
                        "cves": [],
                        "recommended_action": "none",
                        "sources": [],
                    }
                ),
                "usage": {"input_tokens": 20, "output_tokens": 40, "cost_usd": 0.002},
            }
        if "cross-reference reviewer" in system:
            return {
                "text": json.dumps({"status": "pass", "summary": "ok"}),
                "usage": {"input_tokens": 20, "output_tokens": 10, "cost_usd": 0.001},
            }
        raise AssertionError(f"unexpected agent: {system[:60]}")


def _seed_pyproject(tmp_path: Path, deps: list[str]) -> None:
    deps_toml = ",\n    ".join(f'"{d}"' for d in deps)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "0"\ndependencies = [\n    {deps_toml}\n]\n'
    )


async def _run_stage6(state, fake):
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=5.0), sdk=fake)
    return await stage_tech_debt(state=state, runner=runner)


@pytest.mark.anyio
async def test_second_run_with_unchanged_manifest_skips(tmp_path: Path):
    _seed_pyproject(tmp_path, ["requests>=2.31"])
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    fake1 = CountingFakeSDK()
    await _run_stage6(state, fake1)
    assert len(fake1.calls) > 0

    fake2 = CountingFakeSDK()
    await _run_stage6(state, fake2)
    assert fake2.calls == [], f"unchanged manifest made {len(fake2.calls)} LLM calls; should be 0"


@pytest.mark.anyio
async def test_added_dep_regenerates(tmp_path: Path):
    _seed_pyproject(tmp_path, ["requests>=2.31"])
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    await _run_stage6(state, CountingFakeSDK())

    # Add a new dep
    _seed_pyproject(tmp_path, ["requests>=2.31", "pydantic>=2.7"])
    fake = CountingFakeSDK()
    await _run_stage6(state, fake)
    # Regeneration covers ALL deps — researcher + crossref per dep = 4 calls.
    assert len(fake.calls) == 4, f"expected 4 calls for 2 deps, got {len(fake.calls)}"


@pytest.mark.anyio
async def test_removed_dep_regenerates(tmp_path: Path):
    _seed_pyproject(tmp_path, ["requests>=2.31", "pydantic>=2.7"])
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    await _run_stage6(state, CountingFakeSDK())

    _seed_pyproject(tmp_path, ["requests>=2.31"])
    fake = CountingFakeSDK()
    await _run_stage6(state, fake)
    assert len(fake.calls) == 2, "removed dep -> regenerate with remaining"


@pytest.mark.anyio
async def test_missing_ledger_forces_regeneration(tmp_path: Path):
    """If TECH_DEBT.md is deleted manually, a matching hash shouldn't cause a skip."""
    _seed_pyproject(tmp_path, ["requests>=2.31"])
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    await _run_stage6(state, CountingFakeSDK())
    (output / "TECH_DEBT.md").unlink()

    fake = CountingFakeSDK()
    await _run_stage6(state, fake)
    assert len(fake.calls) > 0, "missing TECH_DEBT.md should force regeneration"
