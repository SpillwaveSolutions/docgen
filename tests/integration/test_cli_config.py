"""Integration test for --config flag threading through the pipeline.

Before this test existed, the CLI accepted --config but silently ignored it.
We verify two config-driven behaviors actually take effect:

1. `exclude_paths` from config reaches Stage 0 (files in excluded dirs
   do NOT appear in the discovery tree).
2. `skip_stages` from config merges with the --skip CLI flag.
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


def test_config_exclude_paths_reaches_stage0(tmp_path: Path):
    """A file under a config-declared exclude dir must NOT appear in stage0 output."""
    # Seed a tiny repo with one kept file and one that should be excluded
    (tmp_path / "keep.py").write_text("# keep\n")
    (tmp_path / "generated" / "nope.py").parent.mkdir(parents=True)
    (tmp_path / "generated" / "nope.py").write_text("# nope\n")

    config = tmp_path / ".designdoc.toml"
    config.write_text(
        """
[languages]
exclude_paths = ["generated"]
"""
    )

    output = tmp_path / "design"
    # Skip every LLM stage and mermaid — we only care about stage 0 output here
    result = _run(
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
    # May fail downstream when skipped stages leave an incomplete tree,
    # but stage 0 must have completed and written its output before any failure
    stage0_out = output / "stage0_discovery.json"
    assert stage0_out.exists(), f"stage 0 didn't run: {result.stdout} / {result.stderr}"
    data = json.loads(stage0_out.read_text())
    paths = set(data["tree"])
    assert "keep.py" in paths
    assert not any("generated" in p for p in paths), f"excluded dir leaked: {paths}"


def test_config_skip_stages_merges_with_cli_flag(tmp_path: Path):
    """Stages in config [stages].skip should be skipped alongside --skip flag."""
    (tmp_path / "a.py").write_text("# a\n")

    config = tmp_path / ".designdoc.toml"
    config.write_text(
        """
[stages]
skip = ["file_analysis", "class_docs"]
"""
    )

    output = tmp_path / "design"
    # --skip on CLI adds mermaid, package_rollups etc
    result = _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
            "--output",
            str(output),
            "--config",
            str(config),
            "--skip",
            "mermaid",
            "--skip",
            "package_rollups",
            "--skip",
            "tech_debt",
            "--skip",
            "system_rollup",
        ]
    )

    state = output / ".designdoc-state.json"
    assert state.exists(), f"orchestrator didn't start: {result.stdout} / {result.stderr}"
    state_data = json.loads(state.read_text())
    stages = state_data["stages"]
    # file_analysis and class_docs were in config.skip — must not be DONE
    assert stages.get("file_analysis") != "done"
    assert stages.get("class_docs") != "done"
    # discover + index are not skipped and should have run
    assert stages["discover"] == "done"
    assert stages["index"] == "done"


def test_config_missing_file_raises_clear_error(tmp_path: Path):
    result = _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
            "--config",
            str(tmp_path / "does-not-exist.toml"),
        ]
    )
    assert result.returncode != 0
    # The error surfaces via typer — either stdout or stderr
    combined = (result.stdout + (result.stderr or "")).lower()
    assert "config" in combined or "not found" in combined
