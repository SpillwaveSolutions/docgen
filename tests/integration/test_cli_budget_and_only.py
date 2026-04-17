"""Tests for bug_008: max_budget_usd and only_stages must take effect.

Precedence rule for max_budget_usd:
- If --budget is passed on the CLI, that wins (explicit user intent).
- Otherwise, config.max_budget_usd wins.
- If neither is set, the Config default (5.0) applies.

only_stages: if config sets [stages].only = [...], the orchestrator must
run ONLY those stages (all others skipped, regardless of --skip flag).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "designdoc", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_config_max_budget_usd_is_respected(tmp_path: Path):
    """User sets max_budget_usd = 20 in config, doesn't pass --budget.
    The CostAccumulator must be configured with 20, not the CLI default 5."""
    config = tmp_path / ".designdoc.toml"
    config.write_text(
        """
[pipeline]
max_budget_usd = 20.0
"""
    )
    (tmp_path / "a.py").write_text("# ok\n")
    output = tmp_path / "design"

    _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
            "--output",
            str(output),
            "--config",
            str(config),
            "--skip",
            "file_analysis",
            "--skip",
            "class_docs",
            "--skip",
            "package_rollups",
            "--skip",
            "mermaid",
            "--skip",
            "tech_debt",
            "--skip",
            "system_rollup",
        ]
    )

    budget_file = output / ".designdoc-budget.json"
    assert budget_file.exists(), "pipeline didn't reach budget-write stage"
    data = json.loads(budget_file.read_text())
    assert data["cap_usd"] == 20.0, f"config max_budget_usd was ignored; cap is {data['cap_usd']}"


def test_cli_budget_flag_overrides_config(tmp_path: Path):
    """Explicit --budget beats config."""
    config = tmp_path / ".designdoc.toml"
    config.write_text(
        """
[pipeline]
max_budget_usd = 20.0
"""
    )
    (tmp_path / "a.py").write_text("# ok\n")
    output = tmp_path / "design"

    _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
            "--output",
            str(output),
            "--config",
            str(config),
            "--budget",
            "7.50",
            "--skip",
            "file_analysis",
            "--skip",
            "class_docs",
            "--skip",
            "package_rollups",
            "--skip",
            "mermaid",
            "--skip",
            "tech_debt",
            "--skip",
            "system_rollup",
        ]
    )

    data = json.loads((output / ".designdoc-budget.json").read_text())
    assert data["cap_usd"] == 7.50


def test_config_only_stages_restricts_execution(tmp_path: Path):
    """[stages].only = ['discover'] must run discover and nothing else."""
    config = tmp_path / ".designdoc.toml"
    config.write_text(
        """
[stages]
only = ["discover"]
"""
    )
    (tmp_path / "a.py").write_text("# ok\n")
    output = tmp_path / "design"

    result = _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
            "--output",
            str(output),
            "--config",
            str(config),
        ]
    )
    # Orchestrator should exit cleanly because there's no packages dir error
    # (we never got to Stage 4).
    assert result.returncode == 0, (
        f"only=['discover'] should let the run succeed: {result.stdout} / {result.stderr}"
    )

    state_data = json.loads((output / ".designdoc-state.json").read_text())
    stages = state_data["stages"]
    assert stages.get("discover") == "done"
    # Nothing else should have been executed at all
    for name in (
        "index",
        "file_analysis",
        "class_docs",
        "package_rollups",
        "mermaid",
        "tech_debt",
        "system_rollup",
        "finalize",
    ):
        assert stages.get(name) != "done", (
            f"{name} ran but only_stages=['discover'] should have blocked it"
        )
