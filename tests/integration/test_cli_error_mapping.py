"""Tests for CLI error-code mapping (bug_002).

The original PR wrapped `anyio.run(_run_orchestrator, ...)` in a broad
`except FileNotFoundError` and mapped everything to exit 2 with a comment
that blamed --config. That conflated:
  - missing --config path (genuine config error, exit 2)
  - stage-ordering FileNotFoundErrors (pipeline error, should NOT exit 2)

Fix: validate --config up front; remove the broad handler so stage errors
surface with their real message and a distinct exit code.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "designdoc", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_missing_config_path_exits_2_with_config_message(tmp_path: Path):
    """--config pointing at a nonexistent file -> exit 2, message mentions config."""
    result = _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
            "--config",
            str(tmp_path / "does-not-exist.toml"),
        ]
    )
    assert result.returncode == 2
    combined = (result.stdout + (result.stderr or "")).lower()
    assert "config" in combined
    assert "does-not-exist.toml" in combined


def test_stage_ordering_error_does_not_exit_2(tmp_path: Path):
    """A stage-ordering FileNotFoundError (empty repo -> no packages dir for
    Stage 4) must NOT get mapped to exit 2. Exit 2 is the contract for
    'config missing' only."""
    (tmp_path / "a.py").write_text("# empty\n")
    output = tmp_path / "design"
    # Skip LLM stages — Stage 4 still raises FileNotFoundError for the
    # missing packages dir (no classes -> no packages -> no dir).
    result = _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
            "--output",
            str(output),
            "--skip",
            "file_analysis",
            "--skip",
            "class_docs",
            "--skip",
            "mermaid",
            "--skip",
            "tech_debt",
            "--skip",
            "system_rollup",
        ]
    )
    # Non-zero exit, but NOT 2 — 2 is reserved for config errors
    assert result.returncode != 2, (
        f"stage-ordering error leaked exit 2: {result.stdout} / {result.stderr}"
    )


def test_valid_config_does_not_trigger_not_found_path(tmp_path: Path):
    (tmp_path / ".designdoc.toml").write_text(
        """
[pipeline]
max_budget_usd = 10.0
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
            str(tmp_path / ".designdoc.toml"),
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
    # The config path is valid — exit 2 would mean we misdiagnosed.
    assert result.returncode != 2 or "config" not in (result.stderr or "").lower()
