"""Pydantic schemas for checker output and a malformed-input-safe parser.

The @model_validator is the type-level guard preventing self-grading: a checker
cannot emit status="pass" while reporting major or critical issues.

parse_verdict never raises — any unparseable or schema-violating input returns
a synthetic fail verdict so the doer/checker loop counts it as a failed attempt.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

Severity = Literal["critical", "major", "minor"]
Status = Literal["pass", "fail"]

SYNTH_FAIL_PREFIX = "checker output unparseable: "
"""Prefix used by parse_verdict on synthetic-fail verdicts. Downstream
tooling (INV-001 debug capture) uses this to distinguish parse failures
from genuine checker objections without re-parsing raw output."""

MERMAID_ARTIFACT_PREFIX = "mermaid:"
"""Artifact-id prefix for mermaid diagrams. When a CheckerVerdict's
artifact_id starts with this prefix, parse_verdict coerces its issues
into MermaidIssue so the extra category/node_or_edge fields survive."""


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
    # Union-typed so mermaid issues (which carry extra category/node_or_edge
    # fields) survive pydantic validation intact when the artifact is a
    # mermaid diagram. CheckerIssue is the fallback for non-mermaid issues.
    issues: list[MermaidIssue | CheckerIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistency(self) -> CheckerVerdict:
        if self.status == "pass" and any(i.severity != "minor" for i in self.issues):
            raise ValueError("pass with non-minor issues is invalid")
        if self.status == "fail" and not self.issues:
            raise ValueError("fail with no issues is invalid")
        return self


_CODE_FENCE_RE = re.compile(
    r"^\s*(?:```|~~~)[ \t]*(?:json|JSON)?[ \t]*\n?(.*?)\n?[ \t]*(?:```|~~~)\s*$",
    re.DOTALL,
)


def _strip_code_fence(raw: str) -> str:
    """If raw is wrapped in a markdown code fence, return the inner content.

    INV-001: checker LLMs reliably wrap JSON output in ```json ... ``` despite
    prompts instructing otherwise. This wrapper-repair is non-destructive —
    unfenced input is returned unchanged. The contents of the fence are NOT
    touched; genuinely malformed JSON inside a fence still routes to the
    synthetic-fail path.
    """
    m = _CODE_FENCE_RE.match(raw)
    return m.group(1) if m else raw


def extract_json_object(raw: str) -> str:
    """Extract a JSON object from LLM-flavored output.

    Issue #41: Sonnet emits structured JSON wrapped in code fences, with prose
    preamble ("Here is the analysis:"), and/or with trailing commentary,
    despite system prompts forbidding all of these. Pydantic's
    ``model_validate_json`` rejects every such wrapper, driving Stage 2's
    94% retry rate on real-codebase runs.

    This is a defensive, non-destructive extractor:

    1. Strip an outer code fence if the entire input is fenced (delegates to
       ``_strip_code_fence``).
    2. If the result is already a balanced ``{...}`` object (with optional
       surrounding whitespace), return it stripped.
    3. Otherwise scan for the first balanced ``{...}`` block, respecting
       string literals so braces inside ``"..."`` don't confuse the depth
       counter.
    4. If no balanced object is found, return the original input unchanged
       so the downstream parser raises its native error.

    Never invents content. Pure win — clean inputs are unchanged.
    """
    text = _strip_code_fence(raw)
    start = text.find("{")
    if start == -1:
        return raw

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\" and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return raw


def parse_verdict(raw: str, *, attempt: int, artifact_id: str) -> CheckerVerdict:
    """Parse a checker's raw output into a verdict.

    Never raises. Malformed JSON, schema violations, or wrong-shape inputs all
    yield a synthetic fail verdict with a critical issue — this is the "fail
    loud, not quiet" principle applied to checker output.

    Input is passed through a non-destructive code-fence strip first (INV-001
    defensive parsing). Raw unfenced JSON is unaffected.
    """
    try:
        data = json.loads(_strip_code_fence(raw))
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
            summary=f"{SYNTH_FAIL_PREFIX}{type(e).__name__}",
            issues=[
                CheckerIssue(
                    severity="critical",
                    location="<checker-output>",
                    current_text=raw[:200],
                    suggested_fix="re-run checker — previous output failed to parse",
                )
            ],
        )
