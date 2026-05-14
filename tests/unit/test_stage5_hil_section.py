"""Unit tests for Stage 5's '## Diagram' section builder under HIL fallback.

Background: when mermaid generation exhausts MAX_ATTEMPTS, the artifact ships
with a `<!-- HIL: HIL-NNN -->` marker. v1 kept the ```mermaid fence around
the (failing) source regardless of WHY it failed. That works for LLM-semantic
disputes (the diagram still parses; the dispute is about content quality),
but it's actively harmful for mmdc syntax failures: the fence renders as a
blank/error in every viewer, so the reader sees a code block that produces
nothing AND a TODO marker with no actionable content visible.

Issue #61: when the terminal failure was an mmdc syntax error, drop the
fence entirely and ship only the HIL marker + a 'diagram unavailable' line.
LLM-semantic disputes (mmdc passed, checker rejected for content reasons)
still keep the fence.
"""

from __future__ import annotations

from designdoc.loop import ArtifactResult
from designdoc.stages.s5_mermaid import _build_diagram_section, _is_mmdc_syntax_failure
from designdoc.verdict import CheckerVerdict, MermaidIssue


def _pass_verdict() -> CheckerVerdict:
    return CheckerVerdict(
        status="pass",
        attempt=1,
        artifact_id="mermaid:Foo",
        summary="ok",
    )


def _syntax_fail_verdict() -> CheckerVerdict:
    return CheckerVerdict(
        status="fail",
        attempt=3,
        artifact_id="mermaid:Foo",
        summary="mmdc syntax check failed",
        issues=[
            MermaidIssue(
                severity="critical",
                location="<mermaid source>",
                current_text="A --> B : calls foo(); raises Err",
                suggested_fix="fix syntax error: Lexical error on line 4",
                category="syntax",
            )
        ],
    )


def _semantic_fail_verdict() -> CheckerVerdict:
    return CheckerVerdict(
        status="fail",
        attempt=3,
        artifact_id="mermaid:Foo",
        summary="hallucinated node",
        issues=[
            MermaidIssue(
                severity="major",
                location="class Bogus",
                current_text="class Bogus",
                suggested_fix="remove Bogus — not present in source",
                category="hallucinated_node",
            )
        ],
    )


# ----- _is_mmdc_syntax_failure -------------------------------------------


def test_is_mmdc_syntax_failure_true_when_category_syntax():
    assert _is_mmdc_syntax_failure(_syntax_fail_verdict()) is True


def test_is_mmdc_syntax_failure_false_for_semantic_failure():
    """LLM-semantic categories (hallucinated_node, missing_edge, etc.) are
    NOT syntax failures — mmdc said yes, the LLM checker had content concerns."""
    assert _is_mmdc_syntax_failure(_semantic_fail_verdict()) is False


def test_is_mmdc_syntax_failure_false_for_pass():
    """A passing verdict has no issues; nothing to classify as syntax."""
    assert _is_mmdc_syntax_failure(_pass_verdict()) is False


def test_is_mmdc_syntax_failure_true_when_any_issue_is_syntax():
    """Mixed-category verdict: if even one issue is a syntax issue, treat
    the whole verdict as syntax-failing (the diagram won't render)."""
    verdict = CheckerVerdict(
        status="fail",
        attempt=3,
        artifact_id="mermaid:Foo",
        summary="multiple problems",
        issues=[
            MermaidIssue(
                severity="major",
                location="edge",
                current_text="A --> B",
                suggested_fix="rephrase",
                category="hallucinated_node",
            ),
            MermaidIssue(
                severity="critical",
                location="<mermaid source>",
                current_text="A --> B : x; y",
                suggested_fix="fix lexical error",
                category="syntax",
            ),
        ],
    )
    assert _is_mmdc_syntax_failure(verdict) is True


# ----- _build_diagram_section --------------------------------------------


def _result(status: str, verdict: CheckerVerdict) -> ArtifactResult:
    return ArtifactResult(
        artifact_id="mermaid:Foo",
        status=status,  # type: ignore[arg-type]
        text="classDiagram\n    class Foo\n",
        attempt=verdict.attempt,
        verdict=verdict,
    )


def test_build_diagram_section_pass_emits_fence():
    """The happy path is unchanged: ## Diagram heading + fenced mermaid block."""
    src = "classDiagram\n    class Foo\n    class Bar\n"
    section = _build_diagram_section(_result("pass", _pass_verdict()), src, hil_id=None)

    assert "## Diagram" in section
    assert "```mermaid" in section
    assert "class Foo" in section
    assert "class Bar" in section
    assert "<!-- HIL:" not in section


def test_build_diagram_section_hil_syntax_drops_fence():
    """Terminal mmdc syntax failure → no ```mermaid fence (broken markdown
    in every viewer); just the HIL marker + a 'diagram unavailable' line
    pointing the reader at hil-issues.yaml."""
    src = "classDiagram\n    A --> B : foo(); bar\n"
    section = _build_diagram_section(
        _result("shipped_with_hil", _syntax_fail_verdict()),
        src,
        hil_id="HIL-003",
    )

    assert "## Diagram" in section
    assert "<!-- HIL: HIL-003" in section
    assert "```mermaid" not in section, (
        "syntax-failed diagrams must not ship a broken fence — that renders "
        "as a blank/error block in every viewer"
    )
    assert "HIL-003" in section
    assert "hil-issues.yaml" in section.lower()


def test_build_diagram_section_hil_semantic_keeps_fence():
    """Terminal LLM-semantic failure (mmdc passed, content disputed) → keep
    the fence. The diagram renders fine; the dispute is about quality, not
    parseability."""
    src = "classDiagram\n    class Foo\n    class Bogus\n"
    section = _build_diagram_section(
        _result("shipped_with_hil", _semantic_fail_verdict()),
        src,
        hil_id="HIL-004",
    )

    assert "## Diagram" in section
    assert "<!-- HIL: HIL-004" in section
    assert "```mermaid" in section
    assert "class Foo" in section
    assert "class Bogus" in section
