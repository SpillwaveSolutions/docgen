"""Stage 5: generate + validate mermaid diagrams for every class doc.

Preflight-checks mmdc. For each class doc written in Stage 3, generates one
mermaid diagram, runs the two-checker loop, and appends the validated
```mermaid``` block to the doc.
"""

from __future__ import annotations

from designdoc.hil import inline_comment
from designdoc.mermaid.loop import generate_validated_mermaid, strip_fence
from designdoc.mermaid.mmdc import preflight
from designdoc.state import PipelineState, StageStatus

STAGE_NAME = "mermaid"


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
        artifact_name = class_doc.stem
        result = await generate_validated_mermaid(
            artifact_name=artifact_name,
            artifact_text=text,
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

        class_doc.write_text(text.rstrip() + section)
        diagrams[str(class_doc.relative_to(state.output_dir))] = (
            mermaid_src.splitlines()[0] if mermaid_src else ""
        )

    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 6)
    state.save()
    return diagrams
