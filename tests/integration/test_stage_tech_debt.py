"""Integration: Stage 6 against tiny_repo with a FakeSDK.

tiny_repo has one dep (`requests`) declared in its pyproject.toml. The fake
returns a researcher report + pass verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s6_tech_debt import OUTPUT_FILENAME
from designdoc.stages.s6_tech_debt import run as stage_tech_debt
from designdoc.state import PipelineState, StageStatus

TINY_REPO = Path(__file__).parent.parent / "fixtures" / "tiny_repo"


class FakeSDK:
    async def query(self, *, prompt: str, options: dict) -> dict:
        system = options.get("system_prompt") or ""
        if "tech-debt researcher" in system:
            return {
                "text": json.dumps(
                    {
                        "name": "requests",
                        "pinned": ">=2.31",
                        "latest": "2.32.3",
                        "status": "current",
                        "cves": [],
                        "recommended_action": "none",
                        "sources": ["https://pypi.org/project/requests"],
                    }
                ),
                "usage": {"input_tokens": 20, "output_tokens": 40, "cost_usd": 0.002},
            }
        if "cross-reference reviewer" in system:
            return {
                "text": json.dumps({"status": "pass", "summary": "verified"}),
                "usage": {"input_tokens": 20, "output_tokens": 10, "cost_usd": 0.001},
            }
        raise AssertionError(f"unexpected system prompt: {system[:80]}")


@pytest.mark.anyio
async def test_stage6_writes_tech_debt_markdown(tmp_path: Path):
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=TINY_REPO)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())

    rows = await stage_tech_debt(state=state, runner=runner)

    assert state.stages["tech_debt"] == StageStatus.DONE
    assert len(rows) == 1
    assert rows[0]["name"] == "requests"
    assert rows[0]["status"] == "current"
    assert rows[0]["disputed"] is False

    md = (output / OUTPUT_FILENAME).read_text()
    assert "# Tech Debt Ledger" in md
    assert "requests" in md
    assert "current" in md
    assert "pyproject.toml" in md


@pytest.mark.anyio
async def test_stage6_on_empty_repo_writes_empty_ledger(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())

    rows = await stage_tech_debt(state=state, runner=runner)
    assert rows == []
    md = (output / OUTPUT_FILENAME).read_text()
    assert "# Tech Debt Ledger" in md
