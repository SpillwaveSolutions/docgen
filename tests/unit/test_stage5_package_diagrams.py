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

from designdoc.stages.s5_mermaid import _merge_class_diagrams


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
