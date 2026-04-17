"""Tests for the previously unwired config fields.

Reviewer-flagged in an earlier ultrareview pass:
- include_languages: filters Stage 0 discovery to only these languages.
- output_dir: flows through when --output flag is absent.
- diagram_format: validated (v1 only supports mermaid); other values fail fast.
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


def test_include_languages_filters_discovery(tmp_path: Path):
    """Config [languages].include = ['python'] must drop TS/JS files from Stage 0."""
    (tmp_path / "a.py").write_text("# py\n")
    (tmp_path / "web.ts").write_text("// ts\n")
    (tmp_path / "old.go").write_text("// go\n")

    config = tmp_path / ".designdoc.toml"
    config.write_text(
        """
[languages]
include = ["python"]
"""
    )
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

    data = json.loads((output / "stage0_discovery.json").read_text())
    paths = set(data["tree"])
    assert "a.py" in paths
    assert "web.ts" not in paths, "TS file leaked through include_languages filter"
    assert "old.go" not in paths
    assert data["languages"] == {"python": 1}


def test_output_dir_from_config_used_when_no_flag(tmp_path: Path):
    """Config [output].dir overrides the <repo>/docs/design default when
    --output is absent."""
    (tmp_path / "a.py").write_text("# py\n")

    config = tmp_path / ".designdoc.toml"
    config.write_text(
        """
[output]
dir = "custom/design-docs"
"""
    )

    _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
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

    # The configured output dir should hold the state file
    assert (tmp_path / "custom" / "design-docs" / ".designdoc-state.json").exists()
    # And the default dir should NOT have been created
    assert not (tmp_path / "docs" / "design").exists()


def test_cli_output_flag_beats_config_output_dir(tmp_path: Path):
    """Explicit --output always wins over config."""
    (tmp_path / "a.py").write_text("# py\n")

    config = tmp_path / ".designdoc.toml"
    config.write_text('[output]\ndir = "from-config"\n')

    explicit = tmp_path / "from-cli"

    _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
            "--output",
            str(explicit),
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

    assert (explicit / ".designdoc-state.json").exists()
    assert not (tmp_path / "from-config").exists()


def test_diagram_format_other_than_mermaid_rejected(tmp_path: Path):
    """v1 only supports mermaid. Any other value must fail the CLI fast
    with a clear message."""
    (tmp_path / "a.py").write_text("# py\n")

    config = tmp_path / ".designdoc.toml"
    config.write_text('[output]\ndiagram_format = "plantuml"\n')

    result = _run(["generate", "--repo", str(tmp_path), "--config", str(config)])
    assert result.returncode != 0
    combined = (result.stdout + (result.stderr or "")).lower()
    assert "diagram_format" in combined or "plantuml" in combined or "mermaid" in combined


def test_diagram_format_mermaid_accepted(tmp_path: Path):
    (tmp_path / "a.py").write_text("# py\n")
    config = tmp_path / ".designdoc.toml"
    config.write_text('[output]\ndiagram_format = "mermaid"\n')

    _run(
        [
            "generate",
            "--repo",
            str(tmp_path),
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
    # At minimum, Stage 0 ran — the CLI did not reject the config
    assert (tmp_path / "docs" / "design" / ".designdoc-state.json").exists()
