"""Pydantic schemas for checker output and a malformed-input-safe parser.

The @model_validator is the type-level guard preventing self-grading: a checker
cannot emit status="pass" while reporting major or critical issues.

parse_verdict never raises — any unparseable or schema-violating input returns
a synthetic fail verdict so the doer/checker loop counts it as a failed attempt.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

Severity = Literal["critical", "major", "minor"]
Status = Literal["pass", "fail"]


class CheckerIssue(BaseModel):
    severity: Severity
    location: str
    current_text: str
    suggested_fix: str
    source: str | None = None


class MermaidIssue(CheckerIssue):
    category: Literal["syntax", "hallucinated_node", "missing_edge", "wrong_direction", "too_vague"]
    node_or_edge: str | None = None


class CheckerVerdict(BaseModel):
    status: Status
    attempt: int
    artifact_id: str
    summary: str = ""
    issues: list[CheckerIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistency(self) -> CheckerVerdict:
        if self.status == "pass" and any(i.severity != "minor" for i in self.issues):
            raise ValueError("pass with non-minor issues is invalid")
        if self.status == "fail" and not self.issues:
            raise ValueError("fail with no issues is invalid")
        return self


def parse_verdict(raw: str, *, attempt: int, artifact_id: str) -> CheckerVerdict:
    """Parse a checker's raw output into a verdict.

    Never raises. Malformed JSON, schema violations, or wrong-shape inputs all
    yield a synthetic fail verdict with a critical issue — this is the "fail
    loud, not quiet" principle applied to checker output.
    """
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError(f"expected JSON object, got {type(data).__name__}")
        data["attempt"] = attempt
        data["artifact_id"] = artifact_id
        return CheckerVerdict(**data)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as e:
        return CheckerVerdict(
            status="fail",
            attempt=attempt,
            artifact_id=artifact_id,
            summary=f"checker output unparseable: {type(e).__name__}",
            issues=[
                CheckerIssue(
                    severity="critical",
                    location="<checker-output>",
                    current_text=raw[:200],
                    suggested_fix="re-run checker — previous output failed to parse",
                )
            ],
        )
