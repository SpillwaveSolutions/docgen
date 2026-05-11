"""Stage 5: generate + validate mermaid diagrams for every class doc.

Preflight-checks mmdc. For each class doc written in Stage 3, generates one
mermaid diagram, runs the two-checker loop, and appends the validated
```mermaid``` block to the doc.

v1.1 incremental: hash the class doc's body minus any existing Diagram
section. On match with state.rollup_hashes["mermaid:<rel-path>"], skip
regeneration entirely — the doc is left untouched. This also fixes a
pre-existing bug where re-running Stage 5 on the same doc would append a
second Diagram section.

v1.2 within-stage checkpoint: after each diagram is written, persist an
artifact_index["mermaid:<rel-path>"] entry with the input_hash. On rerun,
if the entry matches and the class doc still has its Diagram section, skip
that doc (zero LLM calls). The v1.1 rollup_hashes entries coexist unchanged.
"""

from __future__ import annotations

import hashlib
import re

from designdoc.hil import inline_comment
from designdoc.io_utils import atomic_write
from designdoc.mermaid.loop import generate_validated_mermaid, strip_fence
from designdoc.mermaid.mmdc import preflight, validate
from designdoc.state import PipelineState, StageStatus, state_lock

STAGE_NAME = "mermaid"

# Matches "## Diagram" through the end of the file (i.e. the whole section).
_DIAGRAM_SECTION_RE = re.compile(r"\n*##\s+Diagram\b.*", re.DOTALL)

# Captures a fenced mermaid block's body (the text between the ``` markers).
_MERMAID_FENCE_RE = re.compile(r"```mermaid\n([\s\S]+?)\n```")

# Mermaid classDiagram relationship operators. Lines containing any of these
# (and not also a class-block opener) are treated as relationships.
_ARROW_OPS = ("-->", "<--", "..>", "<..", "--|>", "<|--", "*--", "--*", "o--", "--o")

# Captures the class-name token from `class Foo` or `class Foo { ... }`.
_CLASS_NAME_RE = re.compile(r"\bclass\s+(\w+)")


async def run(
    *,
    state: PipelineState,
    runner,
    skip_preflight: bool = False,
) -> dict[str, str]:
    """Execute Stage 5. Returns {class_doc_path: mermaid_src_first_line}."""
    if not skip_preflight:
        preflight()  # raises MmdcNotAvailableError if mmdc is missing

    packages_dir = state.output_dir / "packages"
    if not packages_dir.exists():
        raise FileNotFoundError(f"packages dir missing ({packages_dir})")

    state.stages[STAGE_NAME] = StageStatus.RUNNING
    state.save()

    diagrams: dict[str, str] = {}
    for class_doc in sorted(packages_dir.glob("*/classes/*.md")):
        text = class_doc.read_text()
        body = _strip_diagram_section(text)
        rel = str(class_doc.relative_to(state.output_dir))
        rollup_key = f"mermaid:{rel}"
        input_hash = _hash_body(body)

        # v1.2 within-stage skip: artifact_index check (survives mid-stage crash).
        prior = state.artifact_index.get(rollup_key, {})
        if prior.get("input_hash") == input_hash and input_hash != "" and "## Diagram" in text:
            diagrams[rel] = _first_line_of_existing_diagram(text)
            continue

        # v1.1 cross-run skip: rollup_hashes check (unchanged).
        if state.rollup_hashes.get(rollup_key) == input_hash and "## Diagram" in text:
            diagrams[rel] = _first_line_of_existing_diagram(text)
            continue

        artifact_name = class_doc.stem
        result = await generate_validated_mermaid(
            artifact_name=artifact_name,
            artifact_text=body,
            runner=runner,
            hil_sink=state.hil_issues,
            stage_name=STAGE_NAME,
            state=state,
        )

        mermaid_src = strip_fence(result.text)
        section = f"\n\n## Diagram\n\n```mermaid\n{mermaid_src}\n```\n"
        if result.status == "shipped_with_hil":
            hil_id = state.hil_issues[-1]["id"]
            section = (
                f"\n\n## Diagram\n\n"
                f"{inline_comment(hil_id, 'mermaid diagram disputed')}\n\n"
                f"```mermaid\n{mermaid_src}\n```\n"
            )

        # Write body (without any prior Diagram) plus the fresh section —
        # avoids stacking Diagram sections on re-run.
        new_content = body.rstrip() + section
        atomic_write(class_doc, new_content)
        diagrams[rel] = mermaid_src.splitlines()[0] if mermaid_src else ""

        async with state_lock:
            # v1.2 within-stage checkpoint: persist per-class mermaid entry.
            state.artifact_index[rollup_key] = {
                "path": rel,
                "input_hash": input_hash,
            }
            # v1.1 cross-run skip coexists with v1.2 within-stage checkpoint.
            state.rollup_hashes[rollup_key] = input_hash
            state.save()

    # Per-package overview diagrams. Synthesized by merging the per-class
    # diagrams above; appended to each package README so the design-doc
    # reader gets a bird's-eye view without burning extra LLM budget.
    await _emit_package_diagrams(state, packages_dir)

    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 6)
    state.save()
    return diagrams


async def _emit_package_diagrams(state: PipelineState, packages_dir) -> None:
    """For each package, merge its per-class diagrams into a slim overview
    and append to the package README. Skips packages whose inputs haven't
    changed since the last run (within-stage checkpoint) and packages whose
    merged diagram fails mmdc validation (logged, not fatal).
    """
    for pkg_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        pkg_readme = pkg_dir / "README.md"
        if not pkg_readme.exists():
            continue
        class_docs = sorted(pkg_dir.glob("classes/*.md"))
        if not class_docs:
            continue

        blocks = []
        for cd in class_docs:
            m = _MERMAID_FENCE_RE.search(cd.read_text())
            if m:
                blocks.append(m.group(1))
        if not blocks:
            continue

        # Hash the inputs so re-runs skip when nothing changed. Sorted so
        # class-doc ordering doesn't invalidate the cache.
        input_hash = _hash_body("\n---\n".join(sorted(blocks)))
        rel = str(pkg_readme.relative_to(state.output_dir))
        rollup_key = f"mermaid:{rel}"

        pkg_text = pkg_readme.read_text()
        prior = state.artifact_index.get(rollup_key, {})
        if prior.get("input_hash") == input_hash and "## Diagram" in pkg_text:
            continue
        if state.rollup_hashes.get(rollup_key) == input_hash and "## Diagram" in pkg_text:
            continue

        merged = _merge_class_diagrams(blocks)
        if not merged:
            continue

        validation = validate(merged)
        if not validation.ok:
            # Fail soft: leave the README diagram-less rather than crash the
            # stage. Stage 5 is best-effort by design (per-class diagrams
            # already use the same HIL-or-ship pattern).
            continue

        body = _strip_diagram_section(pkg_text)
        section = f"\n\n## Diagram\n\n```mermaid\n{merged}\n```\n"
        atomic_write(pkg_readme, body.rstrip() + section)

        async with state_lock:
            state.artifact_index[rollup_key] = {"path": rel, "input_hash": input_hash}
            state.rollup_hashes[rollup_key] = input_hash
            state.save()


def _merge_class_diagrams(blocks: list[str]) -> str:
    """Merge per-class mermaid classDiagram bodies into a slim package
    overview: deduplicated class-name boxes + deduplicated relationship
    arrows. Inner-class detail (fields / methods) is intentionally dropped —
    that detail belongs in the per-class docs, not in the package overview.

    Blocks that aren't classDiagram-style (e.g. flowchart, sequenceDiagram)
    are ignored so a stray non-class diagram doesn't corrupt the merge.
    Returns an empty string if no class names were found.
    """
    class_names: set[str] = set()
    arrows: set[str] = set()

    for block in blocks:
        # Identify classDiagram blocks by the directive at the head. We do
        # this rather than parse the directive line because the directive
        # may be on the first or second line depending on producer style.
        head = block.strip().splitlines()[0] if block.strip() else ""
        if not head.lower().startswith("classdiagram"):
            continue

        for m in _CLASS_NAME_RE.finditer(block):
            class_names.add(m.group(1))

        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower().startswith("classdiagram"):
                continue
            # A line is a relationship if it contains an arrow operator AND
            # does NOT start with `class ` (otherwise we'd grab class-block
            # openers with `class Foo --> Bar` style inline declarations).
            if any(op in stripped for op in _ARROW_OPS) and not stripped.startswith("class "):
                arrows.add(stripped)

    if not class_names:
        return ""

    lines = ["classDiagram"]
    lines.extend(f"    class {name}" for name in sorted(class_names))
    lines.extend(f"    {arrow}" for arrow in sorted(arrows))
    return "\n".join(lines)


def _strip_diagram_section(text: str) -> str:
    """Return the class doc text with any trailing '## Diagram' section removed."""
    return _DIAGRAM_SECTION_RE.sub("", text).rstrip() + "\n"


def _hash_body(body: str) -> str:
    return hashlib.sha1(body.encode("utf-8")).hexdigest()


def _first_line_of_existing_diagram(text: str) -> str:
    """Extract the first line of the ```mermaid block in an existing doc
    (for the returned diagrams map). Empty string if none found."""
    m = re.search(r"```mermaid\n(.+?)\n", text)
    return m.group(1) if m else ""
