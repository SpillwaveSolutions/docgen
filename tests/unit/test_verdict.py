"""Tests for CheckerVerdict, CheckerIssue, MermaidIssue, and parse_verdict.

The pydantic consistency validator is the type-level guard against self-grading:
- status="pass" with ANY major/critical issue -> rejected
- status="fail" with NO issues -> rejected

parse_verdict must NEVER raise. Any unparseable checker output becomes a
synthetic fail verdict (Gen 3 rule 4: fail loud, not quiet).
"""

from __future__ import annotations

import json

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


# INV-001 regression guards: checker LLMs reliably wrap JSON in markdown code
# fences despite "Return only the JSON, no prose, no code fences" prompts.
# parse_verdict must handle this defensively so pass verdicts wrapped in fences
# are not miscategorized as parse failures.


def test_parse_strips_json_code_fence():
    """```json\\n{...}\\n``` — the exact pattern observed in 9/9 failing captures
    during INV-001 diagnostic dogfood run against tiny_repo."""
    raw = '```json\n{"status":"pass","summary":"ok"}\n```'
    v = parse_verdict(raw, attempt=1, artifact_id="x")
    assert v.status == "pass"
    assert v.summary == "ok"


def test_parse_strips_bare_code_fence():
    """```\\n{...}\\n``` without explicit json language marker."""
    raw = '```\n{"status":"pass","summary":"ok"}\n```'
    v = parse_verdict(raw, attempt=1, artifact_id="x")
    assert v.status == "pass"


def test_parse_strips_fence_with_surrounding_whitespace():
    """LLMs often add leading/trailing whitespace around the fence."""
    raw = '  \n```json\n{"status":"pass"}\n```\n\n'
    v = parse_verdict(raw, attempt=1, artifact_id="x")
    assert v.status == "pass"


def test_parse_fence_wrapped_fail_verdict():
    """Fence-stripping must preserve genuine fail verdicts — we must not turn
    a fenced fail into a synthetic fail (that would lose the real issues)."""
    raw = (
        '```json\n{"status":"fail","issues":[{"severity":"major","location":"x",'
        '"current_text":"c","suggested_fix":"f"}]}\n```'
    )
    v = parse_verdict(raw, attempt=1, artifact_id="x")
    assert v.status == "fail"
    assert len(v.issues) == 1
    assert v.issues[0].severity == "major"
    assert v.issues[0].suggested_fix == "f"


def test_parse_clean_json_unaffected_by_fence_strip():
    """Regression: unwrapped JSON must still parse identically to before."""
    raw = '{"status":"pass","summary":"ok"}'
    v = parse_verdict(raw, attempt=1, artifact_id="x")
    assert v.status == "pass"
    assert v.summary == "ok"


def test_parse_genuinely_malformed_inside_fence_still_fails():
    """Fence-strip is a wrapper-repair, not a JSON repair. Broken JSON inside
    a fence must still route to the synthetic-fail path (fail loud)."""
    raw = "```json\n{broken not valid\n```"
    v = parse_verdict(raw, attempt=1, artifact_id="x")
    assert v.status == "fail"
    assert any(i.severity == "critical" for i in v.issues)


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


def test_parse_verdict_preserves_mermaid_issue_fields():
    """When artifact_id is a mermaid diagram, issues carry category/node_or_edge
    fields and parse_verdict must preserve them — pydantic union resolution
    routes dicts with `category` keys to MermaidIssue rather than CheckerIssue."""
    raw = json.dumps(
        {
            "status": "fail",
            "summary": "semantic check failed",
            "issues": [
                {
                    "severity": "major",
                    "location": "line 7",
                    "current_text": "A-->Z",
                    "suggested_fix": "remove Z; not in source artifact",
                    "category": "hallucinated_node",
                    "node_or_edge": "Z",
                }
            ],
        }
    )
    v = parse_verdict(raw, attempt=1, artifact_id="mermaid:Foo")
    assert v.status == "fail"
    assert len(v.issues) == 1
    issue = v.issues[0]
    assert isinstance(issue, MermaidIssue)
    assert issue.category == "hallucinated_node"
    assert issue.node_or_edge == "Z"


def test_parse_verdict_non_mermaid_issue_unaffected():
    """Issues without `category` field still parse as plain CheckerIssue —
    union fallback works in both directions."""
    raw = json.dumps(
        {
            "status": "fail",
            "summary": "doc failed",
            "issues": [
                {
                    "severity": "major",
                    "location": "intro",
                    "current_text": "lorem",
                    "suggested_fix": "rewrite",
                }
            ],
        }
    )
    v = parse_verdict(raw, attempt=1, artifact_id="path/foo.py::Bar")
    assert v.status == "fail"
    assert isinstance(v.issues[0], CheckerIssue)
    # Not a MermaidIssue (lacks category), so subclass check is False.
    assert not isinstance(v.issues[0], MermaidIssue)
