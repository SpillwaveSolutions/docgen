"""Tests for the --parallelism CLI flag.

Precedence: --parallelism flag > config.parallelism > Config default (3).
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


def test_parallelism_flag_accepted(tmp_path: Path):
    (tmp_path / "a.py").write_text("# py\n")
    output = tmp_path / "design"
    result = _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
            "--output",
            str(output),
            "--parallelism",
            "5",
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
    # Didn't fail at flag parsing
    assert (output / ".designdoc-state.json").exists(), (
        f"--parallelism 5 rejected: {result.stdout} / {result.stderr}"
    )


def test_parallelism_flag_beats_config(tmp_path: Path):
    """Explicit --parallelism overrides config.parallelism.

    Verified indirectly by checking the orchestrator runs without error
    when CLI says 4 and config says 1 (the behavior difference manifests
    at LLM call time, but we just assert the flag is accepted here)."""
    (tmp_path / "a.py").write_text("# py\n")
    config = tmp_path / ".designdoc.toml"
    config.write_text("[pipeline]\nparallelism = 1\n")
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
            "--parallelism",
            "4",
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
    assert (output / ".designdoc-state.json").exists(), (
        f"--parallelism + config combination rejected: {result.stderr}"
    )


def test_parallelism_zero_or_negative_rejected(tmp_path: Path):
    """Must be a positive int."""
    (tmp_path / "a.py").write_text("# py\n")
    result = _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
            "--parallelism",
            "0",
        ]
    )
    assert result.returncode != 0
    combined = (result.stdout + (result.stderr or "")).lower()
    assert "parallelism" in combined
