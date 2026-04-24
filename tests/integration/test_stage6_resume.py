"""Stage 6 within-stage resume: per-dep checkpoint survives mid-stage crash.

Tests that:
1. artifact_index["dep:<name>"] is written after each dep is researched.
2. A second run with unchanged deps makes zero LLM calls (checkpoint fires).
3. A simulated mid-stage crash (partial artifact_index) causes only the
   un-checkpointed deps to be processed on the next run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.io_utils import sha1_keyed
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s6_tech_debt import _dep_hash_items
from designdoc.stages.s6_tech_debt import run as stage_tech_debt
from designdoc.state import PipelineState


class _CountingFakeSDK:
    """Counts LLM calls and returns valid researcher + pass-verdict responses.

    Extracts the dependency name from the prompt ("Dependency: <name>") so the
    returned row carries the real dep name instead of a placeholder.
    """

    def __init__(self) -> None:
        self.call_count: int = 0

    async def query(self, *, prompt: str, options: dict) -> dict:
        self.call_count += 1
        system = options.get("system_prompt") or ""
        # Extract dep name from prompt line "Dependency: <name>".
        dep_name = "stub"
        for line in prompt.splitlines():
            if line.startswith("Dependency:"):
                dep_name = line.split(":", 1)[1].strip()
                break
        if "tech-debt researcher" in system:
            return {
                "text": json.dumps(
                    {
                        "name": dep_name,
                        "pinned": ">=1.0",
                        "latest": "1.1",
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
        raise AssertionError(f"unexpected system prompt: {system[:80]}")


def _seed_pyproject(tmp_path: Path, deps: list[str]) -> None:
    deps_toml = ",\n    ".join(f'"{d}"' for d in deps)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "0"\ndependencies = [\n    {deps_toml}\n]\n'
    )


def _make_runner(sdk: _CountingFakeSDK) -> ClaudeSDKRunner:
    return ClaudeSDKRunner(budget=CostAccumulator(cap_usd=10.0), sdk=sdk)


@pytest.mark.anyio
async def test_stage6_checkpoints_per_dep(tmp_path: Path) -> None:
    """After a successful run, artifact_index has 'dep:<name>' entries
    with non-empty input_hash and serialised row data."""
    _seed_pyproject(tmp_path, ["requests>=2.31"])
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    sdk = _CountingFakeSDK()
    await stage_tech_debt(state=state, runner=_make_runner(sdk))

    assert "dep:requests" in state.artifact_index, (
        "artifact_index must have an entry for 'dep:requests'"
    )
    entry = state.artifact_index["dep:requests"]
    assert entry["input_hash"] != "", "input_hash must be non-empty"
    assert entry["path"] == "TECH_DEBT.md", "path must point to TECH_DEBT.md"
    assert "row" in entry, "entry must contain serialised row data"
    row = json.loads(entry["row"])
    assert "name" in row and "status" in row, "row must contain at least name and status"


@pytest.mark.anyio
async def test_stage6_rerun_skips_checkpointed_deps(tmp_path: Path) -> None:
    """Two-run test: first run produces TECH_DEBT.md + artifact_index entries;
    second run with same dep manifest must make ZERO LLM calls."""
    _seed_pyproject(tmp_path, ["requests>=2.31"])
    output = tmp_path / "design"
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    # First run: LLM is called.
    sdk1 = _CountingFakeSDK()
    await stage_tech_debt(state=state, runner=_make_runner(sdk1))
    assert sdk1.call_count > 0, "first run must make LLM calls"

    # Verify artifact_index entry was written.
    assert "dep:requests" in state.artifact_index
    assert state.artifact_index["dep:requests"]["input_hash"] != ""

    # Reload state from disk (simulating a crash-resume).
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    # Second run: dep manifest unchanged -> zero LLM calls (checkpoint fires).
    sdk2 = _CountingFakeSDK()
    await stage_tech_debt(state=state2, runner=_make_runner(sdk2))
    assert sdk2.call_count == 0, (
        f"unchanged deps made {sdk2.call_count} LLM calls on second run; expected 0"
    )


@pytest.mark.anyio
async def test_stage6_partial_crash_resume(tmp_path: Path) -> None:
    """Simulate a mid-stage crash: pre-populate artifact_index for one dep,
    verify that only the other dep is processed on the next run."""
    from designdoc.index.manifests import Dep

    _seed_pyproject(tmp_path, ["requests>=2.31", "pydantic>=2.7"])
    output = tmp_path / "design"
    output.mkdir(parents=True, exist_ok=True)
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    # Simulate 'requests' having been completed before crash:
    # manually compute its input_hash and plant it in artifact_index with a row.
    requests_dep = Dep(name="requests", pinned=">=2.31", source="pyproject.toml")
    req_hash = sha1_keyed(_dep_hash_items([requests_dep]))
    req_row = {
        "name": "requests",
        "pinned": ">=2.31",
        "latest": "2.32",
        "status": "current",
        "cves": [],
        "recommended_action": "none",
        "sources": [],
        "disputed": False,
        "source_file": "pyproject.toml",
    }
    state.artifact_index["dep:requests"] = {
        "path": "TECH_DEBT.md",
        "input_hash": req_hash,
        "row": json.dumps(req_row),
    }
    state.save()
    # Simulate the partial TECH_DEBT.md that would have been written atomically
    # after 'requests' completed before the crash.
    from designdoc.stages.s6_tech_debt import _render_markdown

    (output / "TECH_DEBT.md").write_text(_render_markdown([req_row]))

    # Now run: only 'pydantic' should be processed.
    sdk = _CountingFakeSDK()
    rows = await stage_tech_debt(state=state, runner=_make_runner(sdk))

    # Researcher + crossref = 2 calls for pydantic only.
    assert sdk.call_count == 2, f"expected 2 LLM calls for pydantic only, got {sdk.call_count}"
    assert len(rows) == 2, f"expected 2 rows (requests + pydantic), got {len(rows)}"
    dep_names = {r["name"] for r in rows}
    assert "requests" in dep_names, "'requests' row should be in final output (from checkpoint)"
    assert "pydantic" in dep_names, "'pydantic' row should be in final output (freshly processed)"

    # Both deps now checkpointed.
    assert "dep:requests" in state.artifact_index
    assert "dep:pydantic" in state.artifact_index
