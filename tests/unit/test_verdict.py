"""Tests for CheckerVerdict, CheckerIssue, MermaidIssue, and parse_verdict.

The pydantic consistency validator is the type-level guard against self-grading:
- status="pass" with ANY major/critical issue -> rejected
- status="fail" with NO issues -> rejected

parse_verdict must NEVER raise. Any unparseable checker output becomes a
synthetic fail verdict (Gen 3 rule 4: fail loud, not quiet).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from designdoc.verdict import CheckerIssue, CheckerVerdict, MermaidIssue, parse_verdict


def test_valid_pass_no_issues():
    v = CheckerVerdict(status="pass", attempt=1, artifact_id="a", summary="ok")
    assert v.status == "pass"
    assert v.issues == []


def test_valid_pass_with_minor_issue_only():
    """Minor issues on a pass are allowed — they surface in the doc as follow-ups."""
    v = CheckerVerdict(
        status="pass",
        attempt=1,
        artifact_id="a",
        issues=[
            CheckerIssue(
                severity="minor", location="x:1", current_text="nit", suggested_fix="polish"
            )
        ],
    )
    assert v.status == "pass"


def test_valid_fail_with_issues():
    v = CheckerVerdict(
        status="fail",
        attempt=1,
        artifact_id="a",
        issues=[
            CheckerIssue(
                severity="major", location="x:1", current_text="wrong", suggested_fix="fix"
            )
        ],
    )
    assert v.status == "fail"


def test_pass_with_major_issue_rejected():
    with pytest.raises(ValidationError, match="pass with non-minor"):
        CheckerVerdict(
            status="pass",
            attempt=1,
            artifact_id="a",
            issues=[
                CheckerIssue(severity="major", location="x:1", current_text="a", suggested_fix="b")
            ],
        )


def test_pass_with_critical_issue_rejected():
    with pytest.raises(ValidationError, match="pass with non-minor"):
        CheckerVerdict(
            status="pass",
            attempt=1,
            artifact_id="a",
            issues=[
                CheckerIssue(
                    severity="critical", location="x:1", current_text="a", suggested_fix="b"
                )
            ],
        )


def test_fail_without_issues_rejected():
    with pytest.raises(ValidationError, match="fail with no issues"):
        CheckerVerdict(status="fail", attempt=1, artifact_id="a")


def test_parse_valid_json():
    raw = '{"status":"pass","summary":"ok"}'
    v = parse_verdict(raw, attempt=1, artifact_id="x")
    assert v.status == "pass"
    assert v.attempt == 1
    assert v.artifact_id == "x"


def test_parse_malformed_json_returns_synthetic_fail():
    v = parse_verdict("not json at all", attempt=2, artifact_id="a")
    assert v.status == "fail"
    assert v.attempt == 2
    assert v.artifact_id == "a"
    assert any(i.severity == "critical" for i in v.issues)


def test_parse_schema_violation_returns_synthetic_fail():
    """Valid JSON that violates the consistency rules must also synthetic-fail."""
    raw = '{"status":"fail","issues":[]}'  # fail with no issues — invalid
    v = parse_verdict(raw, attempt=1, artifact_id="a")
    assert v.status == "fail"
    assert any(i.severity == "critical" for i in v.issues)


def test_parse_never_raises_on_any_input():
    """Fuzz-style: None-shaped, type-wrong, empty — must all yield a verdict."""
    for bad in ["", "{}", "null", "[]", '{"status": 42}', '{"status":"maybe"}']:
        v = parse_verdict(bad, attempt=1, artifact_id="a")
        assert v.status == "fail"


def test_mermaid_issue_category():
    m = MermaidIssue(
        severity="major",
        location="line 3",
        current_text="A-->B",
        suggested_fix="remove B",
        category="hallucinated_node",
        node_or_edge="B",
    )
    assert m.category == "hallucinated_node"
    assert m.node_or_edge == "B"
