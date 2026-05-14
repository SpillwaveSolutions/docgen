"""Unit tests for Stage 5's per-package classDiagram merger.

Stage 5 generates one mermaid diagram per class. The package READMEs and
the top-level system rollup had no diagrams at all — readers got prose-only
design docs. The merger takes the per-class diagrams from a package and
synthesizes a slim package-overview classDiagram (class names + relationship
arrows, no inner-class detail), which Stage 5 appends to each package README.

Detailed class internals already live in the per-class docs; the package
overview is for the bird's-eye view.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from designdoc.stages import s5_mermaid
from designdoc.stages.s5_mermaid import (
    _merge_class_diagrams,
    _parse_arrow,
    _strip_arrow_labels,
)


def test_merge_extracts_class_names_from_inputs():
    """Each class declared in any input shows up exactly once."""
    a = "classDiagram\n    class Gateway {\n        +charge()\n    }\n"
    b = "classDiagram\n    class Charge {\n        +amount: float\n    }\n"

    merged = _merge_class_diagrams([a, b])

    assert "classDiagram" in merged
    assert "class Charge" in merged
    assert "class Gateway" in merged


def test_merge_drops_inner_class_detail():
    """The package overview is a bird's-eye view — fields and methods stay
    in the per-class docs, not in the package README's overview diagram."""
    a = (
        "classDiagram\n"
        "    class Gateway {\n"
        "        +charge(card: Card) Charge\n"
        "        -log(msg: str) void\n"
        "        +api_key: str\n"
        "    }\n"
    )

    merged = _merge_class_diagrams([a])

    assert "class Gateway" in merged
    # detail bodies are intentionally elided from the slim overview
    assert "charge(card: Card)" not in merged
    assert "api_key" not in merged


def test_merge_dedupes_class_names_across_inputs():
    """A class referenced from two sibling class docs (e.g., both Gateway.md
    and Charge.md mention `class Card`) appears once in the merge."""
    a = "classDiagram\n    class Gateway\n    class Card\n    Gateway --> Card\n"
    b = "classDiagram\n    class Charge\n    class Card\n    Charge --> Card\n"

    merged = _merge_class_diagrams([a, b])

    # Card declared once across the merged output
    assert merged.count("class Card") == 1
    assert "class Gateway" in merged
    assert "class Charge" in merged


def test_merge_preserves_relationship_arrows():
    """Inheritance, composition, dependency arrows survive the merge."""
    a = (
        "classDiagram\n"
        "    class Animal\n"
        "    class Dog\n"
        "    Animal <|-- Dog\n"
        "    Dog ..> Bone : fetches\n"
    )

    merged = _merge_class_diagrams([a])

    assert "Animal <|-- Dog" in merged
    assert "Dog ..> Bone" in merged


def test_merge_dedupes_identical_arrows():
    """Two class docs both declaring `Gateway --> Card` should produce one
    arrow in the merge, not two."""
    a = "classDiagram\n    class Gateway\n    class Card\n    Gateway --> Card\n"
    b = "classDiagram\n    class Gateway\n    class Card\n    Gateway --> Card\n"

    merged = _merge_class_diagrams([a, b])

    assert merged.count("Gateway --> Card") == 1


def test_merge_returns_empty_on_empty_input():
    """No class blocks → nothing to draw; caller can skip the README append."""
    assert _merge_class_diagrams([]) == ""
    assert _merge_class_diagrams(["classDiagram\n"]) == ""


def test_merge_ignores_non_classdiagram_blocks():
    """Stage 5 sometimes produces flowchart-style diagrams (the FakeSDK in
    integration tests does this). The merger should ignore blocks that
    aren't classDiagram-style rather than corrupt the package overview."""
    a = "classDiagram\n    class Gateway\n"
    b = "flowchart TD\n    A --> B\n"

    merged = _merge_class_diagrams([a, b])

    assert "class Gateway" in merged
    assert "flowchart" not in merged
    assert "A --> B" not in merged


# ----- label-stripping retry (issue #59) -----------------------------------


def test_strip_arrow_labels_removes_text_after_colon_on_arrow_lines():
    """The agent-brain dogfood found `Settings ..> ValidationError : calls
    get_api_key(); raises ValidationError` — `;` in the label broke mmdc.
    Stripping everything after the `:` on arrow lines is the universal
    escape hatch."""
    text = (
        "classDiagram\n"
        "    class Settings\n"
        "    class ValidationError\n"
        "    Settings ..> ValidationError : calls get_api_key(); raises ValidationError\n"
    )

    stripped = _strip_arrow_labels(text)

    assert "Settings ..> ValidationError" in stripped
    arrow_line = next(
        line for line in stripped.splitlines() if "Settings ..> ValidationError" in line
    )
    assert ":" not in arrow_line, f"label not stripped from: {arrow_line!r}"
    assert "calls get_api_key" not in stripped


def test_strip_arrow_labels_leaves_unlabelled_arrows_alone():
    """An arrow line with no `:` should be returned untouched — leading
    indentation (which mermaid renders) preserved, no dropped lines."""
    text = "classDiagram\n    class A\n    class B\n    A --> B\n"

    stripped = _strip_arrow_labels(text)

    assert "A --> B" in stripped
    # Leading whitespace stays put — only the trailing `: label` is removed.
    arrow_line = next(line for line in stripped.splitlines() if "A --> B" in line)
    assert arrow_line == "    A --> B"


def test_strip_arrow_labels_preserves_non_arrow_lines():
    """`class Foo` declarations contain no arrow ops, so their bodies (and
    any `:` inside) must survive intact."""
    text = (
        "classDiagram\n"
        "    class Foo {\n"
        "        +bar: str\n"
        "    }\n"
        "    class Bar\n"
        "    Foo --> Bar : uses\n"
    )

    stripped = _strip_arrow_labels(text)

    # class-body `:` is preserved
    assert "+bar: str" in stripped
    # arrow-line label is stripped
    assert "Foo --> Bar" in stripped
    assert ": uses" not in stripped


@pytest.mark.anyio
async def test_emit_package_diagrams_retries_with_stripped_labels(tmp_path: Path, monkeypatch):
    """When the first merged diagram fails mmdc validation, _emit_package_diagrams
    should retry once with arrow labels stripped, and write the stripped
    diagram if that retry passes. Without this, packages with one parse-unfriendly
    label ship diagram-less even though the merge logic produced something useful."""
    from designdoc.mermaid.mmdc import ValidationResult
    from designdoc.state import PipelineState

    output = tmp_path / "design"
    pkg_dir = output / "packages" / "config"
    classes_dir = pkg_dir / "classes"
    classes_dir.mkdir(parents=True)

    # Two class docs whose mermaid blocks merge into a diagram that contains
    # a label. Whether the label is real-world-toxic doesn't matter — we
    # mock validate() below to simulate mmdc rejecting it.
    (classes_dir / "Settings.md").write_text(
        "# Settings\n\n## Diagram\n\n"
        "```mermaid\n"
        "classDiagram\n"
        "    class Settings\n"
        "    class ValidationError\n"
        "    Settings ..> ValidationError : calls get_api_key(); raises\n"
        "```\n"
    )
    (classes_dir / "Loader.md").write_text(
        "# Loader\n\n## Diagram\n\n"
        "```mermaid\n"
        "classDiagram\n"
        "    class Loader\n"
        "    class Settings\n"
        "    Loader --> Settings : builds\n"
        "```\n"
    )
    (pkg_dir / "README.md").write_text("# config\n\n## Overview\nConfig handling.\n")

    # Capture every input handed to validate so we can assert the second
    # call received label-stripped text. First call → fail, second → pass.
    calls: list[str] = []

    def fake_validate(text: str, *, timeout: float = 30.0):
        calls.append(text)
        if len(calls) == 1:
            return ValidationResult(ok=False, stderr="Parse error (simulated)")
        return ValidationResult(ok=True, stderr="")

    monkeypatch.setattr(s5_mermaid, "validate", fake_validate)

    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await s5_mermaid._emit_package_diagrams(state, output / "packages")

    readme_text = (pkg_dir / "README.md").read_text()
    assert "## Diagram" in readme_text, "label-strip retry must succeed and write a Diagram section"
    assert "```mermaid" in readme_text
    # First call had labels (": calls get_api_key" or ": builds"); the
    # second call (which we approved) had them stripped.
    assert len(calls) == 2, "expected one retry after the first failure"
    assert ":" in calls[0].split("Settings ..> ValidationError", 1)[1].splitlines()[0]
    # The written README contains the stripped version, not the labelled one.
    assert "calls get_api_key" not in readme_text
    assert "builds" not in readme_text.split("## Diagram", 1)[1]


@pytest.mark.anyio
async def test_emit_package_diagrams_stays_failsoft_when_retry_also_fails(
    tmp_path: Path, monkeypatch
):
    """If both the original merge AND the label-stripped retry fail mmdc,
    behaviour should fall back to the v1 fail-soft path: leave the README
    diagram-less rather than crash the stage."""
    from designdoc.mermaid.mmdc import ValidationResult
    from designdoc.state import PipelineState

    output = tmp_path / "design"
    pkg_dir = output / "packages" / "config"
    classes_dir = pkg_dir / "classes"
    classes_dir.mkdir(parents=True)
    (classes_dir / "A.md").write_text(
        "# A\n\n## Diagram\n\n"
        "```mermaid\n"
        "classDiagram\n    class A\n    class B\n    A --> B : x\n"
        "```\n"
    )
    pre_readme = "# config\n\n## Overview\nx\n"
    (pkg_dir / "README.md").write_text(pre_readme)

    monkeypatch.setattr(
        s5_mermaid,
        "validate",
        lambda text, *, timeout=30.0: ValidationResult(ok=False, stderr="fail"),
    )

    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await s5_mermaid._emit_package_diagrams(state, output / "packages")

    assert (pkg_dir / "README.md").read_text() == pre_readme, (
        "README must be untouched when both validate attempts fail"
    )


# ----- arrow dedupe by (src, op, dst) tuple (issue #62) --------------------


def test_parse_arrow_extracts_src_op_dst_label():
    """The basic shape: indented `A --> B : label` → ('A', '-->', 'B', 'label')."""
    assert _parse_arrow("    A --> B : uses") == ("A", "-->", "B", "uses")


def test_parse_arrow_no_label_means_empty_label():
    """An unlabelled arrow returns '' for the label — not None — so callers
    can dedupe-by-tuple uniformly."""
    assert _parse_arrow("    A --> B") == ("A", "-->", "B", "")


def test_parse_arrow_recognizes_all_relationship_ops():
    """Every classDiagram relationship op in _ARROW_OPS must parse cleanly.
    Catches regressions if someone adds a new op to _ARROW_OPS without
    updating the parser."""
    cases = [
        ("    Animal <|-- Dog", "<|--"),
        ("    Dog --|> Animal", "--|>"),
        ("    Dog ..> Bone", "..>"),
        ("    Bone <.. Dog", "<.."),
        ("    Engine *-- Car", "*--"),
        ("    Car --* Engine", "--*"),
        ("    Listener o-- Source", "o--"),
        ("    Source --o Listener", "--o"),
        ("    A --> B", "-->"),
        ("    B <-- A", "<--"),
    ]
    for line, expected_op in cases:
        parsed = _parse_arrow(line)
        assert parsed is not None, f"failed to parse {line!r}"
        assert parsed[1] == expected_op, f"wrong op for {line!r}: got {parsed[1]}"


def test_parse_arrow_returns_none_for_non_arrow_line():
    """`class Foo` and similar non-arrow lines should return None so the
    merger can skip them without misclassifying."""
    assert _parse_arrow("    class Foo") is None
    assert _parse_arrow("classDiagram") is None
    assert _parse_arrow("    ") is None


def test_merge_dedupes_arrows_with_different_labels():
    """The agent-brain dogfood found `BaseModel <|-- GraphQueryContext : extends`
    and `BaseModel <|-- GraphQueryContext : inherits` declared back-to-back
    in models. Set-based dedupe keyed on full line string lets them both
    survive. After this fix, dedupe-by-(src, op, dst) tuple collapses them
    to one arrow (first label wins, deterministic given stable input order)."""
    a = (
        "classDiagram\n"
        "    class BaseModel\n"
        "    class GraphQueryContext\n"
        "    BaseModel <|-- GraphQueryContext : extends\n"
        "    BaseModel <|-- GraphQueryContext : inherits\n"
    )

    merged = _merge_class_diagrams([a])

    arrow_lines = [
        line for line in merged.splitlines() if "BaseModel <|-- GraphQueryContext" in line
    ]
    assert len(arrow_lines) == 1, (
        f"expected 1 arrow after tuple-dedupe, got {len(arrow_lines)}: {arrow_lines!r}"
    )
    # The first label seen wins — deterministic, given that block iteration
    # order is stable (sorted class_docs in the caller).
    assert "extends" in arrow_lines[0]


def test_merge_keeps_distinct_edges_with_different_ops():
    """Same (src, dst) but different op = genuinely distinct edge. Both must
    survive — e.g. `A --> B` (dependency) and `A ..> B` (transient use)
    convey different meaning and shouldn't be collapsed."""
    a = "classDiagram\n    class A\n    class B\n    A --> B\n    A ..> B\n"

    merged = _merge_class_diagrams([a])

    assert "A --> B" in merged
    assert "A ..> B" in merged


def test_merge_keeps_distinct_edges_with_different_targets():
    """Same (src, op) but different dst = different edge. Both survive."""
    a = "classDiagram\n    class A\n    class B\n    class C\n    A --> B\n    A --> C\n"

    merged = _merge_class_diagrams([a])

    assert "A --> B" in merged
    assert "A --> C" in merged
