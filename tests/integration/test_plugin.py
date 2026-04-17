"""Integration: plugin file integrity.

The plugin is a thin shell over the CLI — these tests verify the shipped
files are well-formed (valid JSON, required frontmatter fields, references
to $ARGUMENTS + AskUserQuestion) so a broken plugin gets caught in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent.parent / "plugins" / "designdoc"


def test_plugin_json_is_valid_and_references_command():
    data = json.loads((PLUGIN_ROOT / "plugin.json").read_text())
    assert data["name"] == "designdoc"
    assert "version" in data
    assert data["commands"] == ["./commands/designdoc.md"]


def test_command_file_has_required_frontmatter():
    body = (PLUGIN_ROOT / "commands" / "designdoc.md").read_text()
    assert body.startswith("---\n")
    # Required frontmatter fields
    head = body.split("---\n", 2)[1]
    assert "description:" in head
    assert "argument-hint:" in head
    assert "allowed-tools:" in head
    assert "AskUserQuestion" in head  # resolve needs this
    assert "Bash(designdoc:" in head


def test_command_file_references_all_subcommands():
    body = (PLUGIN_ROOT / "commands" / "designdoc.md").read_text()
    for sub in ("generate", "resume", "status", "resolve"):
        assert sub in body


def test_command_file_documents_hil_walk():
    body = (PLUGIN_ROOT / "commands" / "designdoc.md").read_text()
    assert "hil-issues.yaml" in body
    assert "suggested_fixes" in body
    assert "resolved" in body


def test_plugin_readme_exists_and_links_repo():
    readme = (PLUGIN_ROOT / "README.md").read_text()
    assert "/designdoc generate" in readme
    assert "ANTHROPIC_API_KEY" in readme
