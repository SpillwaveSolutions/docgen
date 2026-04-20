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
from designdoc.mermaid.mmdc import preflight
from designdoc.state import PipelineState, StageStatus, state_lock

STAGE_NAME = "mermaid"

# Matches "## Diagram" through the end of the file (i.e. the whole section).
_DIAGRAM_SECTION_RE = re.compile(r"\n*##\s+Diagram\b.*", re.DOTALL)


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

    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 6)
    state.save()
    return diagrams


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
