"""Tests for HIL issue model, YAML append, and inline-comment helper.

Invariant: append_issue must preserve existing issues when invoked repeatedly.
If this regresses, HIL issues from earlier pipeline runs get lost on the next run.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from designdoc.hil import HILIssue, append_issue, inline_comment


def _load(path: Path) -> dict:
    return YAML(typ="safe").load(path.read_text())


def test_new_file_written_with_header(tmp_path: Path):
    p = tmp_path / "hil-issues.yaml"
    append_issue(
        p,
        HILIssue(
            id="HIL-001",
            artifact="x.md",
            stage="class-docs",
            severity="major",
            doer_said="a",
            checker_said="b",
            attempts=3,
            status="open",
        ),
    )
    doc = _load(p)
    assert doc["version"] == 1
    assert doc["unresolved_count"] == 1
    assert "generated_at" in doc
    assert len(doc["issues"]) == 1
    assert doc["issues"][0]["id"] == "HIL-001"


def test_append_preserves_existing_issue_and_increments_count(tmp_path: Path):
    p = tmp_path / "hil-issues.yaml"
    append_issue(
        p,
        HILIssue(
            id="HIL-001",
            artifact="a",
            stage="s",
            severity="major",
            doer_said="",
            checker_said="",
            attempts=3,
            status="open",
        ),
    )
    append_issue(
        p,
        HILIssue(
            id="HIL-002",
            artifact="b",
            stage="s",
            severity="minor",
            doer_said="",
            checker_said="",
            attempts=3,
            status="open",
        ),
    )
    doc = _load(p)
    assert doc["unresolved_count"] == 2
    assert [i["id"] for i in doc["issues"]] == ["HIL-001", "HIL-002"]


def test_unresolved_count_tracks_open_only(tmp_path: Path):
    """Only status=open issues should count toward unresolved_count."""
    p = tmp_path / "hil-issues.yaml"
    append_issue(
        p,
        HILIssue(
            id="HIL-001",
            artifact="a",
            stage="s",
            severity="major",
            doer_said="",
            checker_said="",
            attempts=3,
            status="resolved",
        ),
    )
    append_issue(
        p,
        HILIssue(
            id="HIL-002",
            artifact="b",
            stage="s",
            severity="major",
            doer_said="",
            checker_said="",
            attempts=3,
            status="open",
        ),
    )
    doc = _load(p)
    assert doc["unresolved_count"] == 1


def test_inline_comment_format():
    got = inline_comment("HIL-042", "retry policy")
    assert got == "<!-- HIL: HIL-042 — retry policy, see hil-issues.yaml -->"


def test_inline_comment_note_is_trimmed():
    got = inline_comment("HIL-001", "  has leading and trailing spaces  ")
    assert got == "<!-- HIL: HIL-001 — has leading and trailing spaces, see hil-issues.yaml -->"


def test_hil_issue_with_suggested_fixes(tmp_path: Path):
    p = tmp_path / "hil-issues.yaml"
    append_issue(
        p,
        HILIssue(
            id="HIL-001",
            artifact="a.md",
            stage="mermaid",
            severity="major",
            doer_said="diagram",
            checker_said="hallucinated node",
            attempts=3,
            status="open",
            suggested_fixes=["remove node B", "rename to C", "add legend"],
        ),
    )
    doc = _load(p)
    assert doc["issues"][0]["suggested_fixes"] == ["remove node B", "rename to C", "add legend"]
