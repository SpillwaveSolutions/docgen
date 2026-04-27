"""Integration: plugin file integrity.

The plugin is a thin shell over the CLI — these tests verify the shipped
files are well-formed (valid JSON, required frontmatter fields, references
to $ARGUMENTS + AskUserQuestion) so a broken plugin gets caught in CI.

Layout (Claude Code marketplace convention):

  <repo>/.claude-plugin/marketplace.json   — declares this repo as a marketplace
  <repo>/plugins/designdoc/.claude-plugin/plugin.json — the plugin manifest
  <repo>/plugins/designdoc/commands/designdoc.md      — the slash command body
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "designdoc"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_JSON = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"


def test_marketplace_json_is_valid_and_lists_designdoc():
    data = json.loads(MARKETPLACE_JSON.read_text())
    assert data["name"], "marketplace must have a name"
    assert isinstance(data.get("plugins"), list) and data["plugins"], (
        "marketplace must list at least one plugin"
    )
    designdoc = next((p for p in data["plugins"] if p.get("name") == "designdoc"), None)
    assert designdoc is not None, "marketplace must list the designdoc plugin"
    # source path must point at an existing plugin folder relative to the repo root
    source = (REPO_ROOT / designdoc["source"]).resolve()
    assert source == PLUGIN_ROOT.resolve(), (
        f"plugin source {designdoc['source']} must point to plugins/designdoc"
    )


def test_plugin_json_lives_under_claude_plugin_dir():
    """Canonical Claude Code plugin layout: manifest under .claude-plugin/."""
    assert PLUGIN_JSON.exists(), (
        f"plugin manifest must live at {PLUGIN_JSON.relative_to(REPO_ROOT)} "
        "(Claude Code marketplace convention)"
    )


def test_plugin_json_is_valid_and_references_command():
    data = json.loads(PLUGIN_JSON.read_text())
    assert data["name"] == "designdoc"
    assert "version" in data
    assert data["commands"] == ["../commands/designdoc.md"], (
        "commands path must be relative to plugin.json (../ to escape .claude-plugin/)"
    )


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
