"""Integration: Stage 8 walks a pre-populated output tree.

Deterministic, no LLM required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from designdoc.stages.s8_finalize import HIL_FILENAME, README_FILENAME
from designdoc.stages.s8_finalize import run as stage_finalize
from designdoc.state import PipelineState, StageStatus


def _seed_output(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "SYSTEM_DESIGN.md").write_text("## Overview\n...")
    (out / "ARCHITECTURE.md").write_text("## Containers\n...")
    (out / "TECH_DEBT.md").write_text("# Tech Debt Ledger\n...")

    pkg = out / "packages" / "payments"
    (pkg / "classes").mkdir(parents=True)
    (pkg / "README.md").write_text("## Overview\npayments package")
    (pkg / "classes" / "Gateway.md").write_text("## Purpose\nGateway")
    (pkg / "classes" / "Charge.md").write_text("## Purpose\nCharge")


@pytest.mark.anyio
async def test_stage8_writes_readme_toc(tmp_path: Path):
    output = tmp_path / "design"
    _seed_output(output)
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    result = await stage_finalize(state=state)

    assert state.stages["finalize"] == StageStatus.DONE
    assert result["readme"] == README_FILENAME
    readme = (output / README_FILENAME).read_text()
    assert "# Design Documentation" in readme
    assert "SYSTEM_DESIGN.md" in readme
    assert "ARCHITECTURE.md" in readme
    assert "TECH_DEBT.md" in readme
    assert "packages/payments/README.md" in readme
    assert "packages/payments/classes/Gateway.md" in readme
    assert "packages/payments/classes/Charge.md" in readme


@pytest.mark.anyio
async def test_stage8_emits_hil_yaml_when_issues_exist(tmp_path: Path):
    output = tmp_path / "design"
    _seed_output(output)
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    state.hil_issues = [
        {
            "id": "HIL-001",
            "artifact": "packages/payments/classes/Gateway.md",
            "stage": "class_docs",
            "severity": "major",
            "doer_said": "Retries up to 3 times.",
            "checker_said": "No retry cap found in source.",
            "attempts": 3,
            "status": "open",
            "suggested_fixes": ["re-read lines 118-140", "confirm wrapper"],
        }
    ]

    result = await stage_finalize(state=state)
    assert result["hil"] == HIL_FILENAME
    hil_body = (output / HIL_FILENAME).read_text()
    assert "HIL-001" in hil_body
    assert "Gateway.md" in hil_body
    assert "unresolved_count: 1" in hil_body


@pytest.mark.anyio
async def test_stage8_skips_hil_yaml_when_no_issues(tmp_path: Path):
    output = tmp_path / "design"
    _seed_output(output)
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    result = await stage_finalize(state=state)
    assert result["hil"] == ""
    assert not (output / HIL_FILENAME).exists()


@pytest.mark.anyio
async def test_stage8_handles_missing_upstream_artifacts(tmp_path: Path):
    """If Stage 7 didn't run, the TOC should still write with just what exists."""
    output = tmp_path / "design"
    output.mkdir(parents=True)
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    result = await stage_finalize(state=state)
    readme = (output / result["readme"]).read_text()
    assert "# Design Documentation" in readme
    # No crash — TOC just doesn't list missing files
