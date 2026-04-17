"""Subprocess-level CLI tests.

Exercises the real `designdoc` console script (as installed by uv) rather than
the in-process typer app. If this passes, the pyproject.toml entry point works.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "designdoc", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_designdoc_help_resolves_via_console_script():
    result = _run(["--help"])
    assert result.returncode == 0
    assert "generate" in result.stdout
    assert "resume" in result.stdout
    assert "status" in result.stdout
    assert "resolve" in result.stdout


def test_designdoc_status_on_fresh_repo(tmp_path: Path):
    result = _run(["status", "--repo", str(tmp_path)])
    assert result.returncode == 0
    assert "no state" in result.stdout.lower()
