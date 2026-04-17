"""Stage 3: per-class design docs.

For each class in Stage 1's signatures, the class-documenter produces a
markdown doc and the doc-quality-checker verifies every claim against the
source file. Full doer/checker loop with 3-attempt cap.

Output path per class: <output>/packages/<pkg>/classes/<Class>.md

v1.1 incremental behavior: when state.prev_hashes shows a source file's
SHA1 is unchanged AND the corresponding class doc exists on disk, the
doer/checker loop is skipped for every class in that file.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from designdoc.agents.class_documenter import (
    build_prompt as build_class_prompt,
)
from designdoc.agents.class_documenter import (
    make_class_documenter,
)
from designdoc.agents.doc_quality_checker import (
    build_prompt as build_checker_prompt,
)
from designdoc.agents.doc_quality_checker import (
    make_doc_quality_checker,
)
from designdoc.hil import inline_comment
from designdoc.loop import doer_checker_loop
from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.stages.s1_index import OUTPUT_FILENAME as STAGE1_FILENAME
from designdoc.state import PipelineState, StageStatus

STAGE_NAME = "class_docs"
OUTPUT_SUBDIR = "packages"


async def run(
    *,
    state: PipelineState,
    runner,
    doer_model: str = "claude-sonnet-4-6",
    checker_model: str = "claude-sonnet-4-6",
    parallelism: int = 1,
) -> dict[str, str]:
    """Execute Stage 3. Returns {class_id: output_path}.

    `parallelism` caps concurrent per-class doer/checker loops."""
    stage1_path = state.output_dir / STAGE1_FILENAME
    if not stage1_path.exists():
        raise FileNotFoundError(f"stage 1 output missing ({stage1_path})")

    state.stages[STAGE_NAME] = StageStatus.RUNNING
    state.save()

    signatures = json.loads(stage1_path.read_text())
    doer = make_class_documenter(model=doer_model)
    checker = make_doc_quality_checker(model=checker_model)

    unchanged_sources = _unchanged_source_paths(state)

    written: dict[str, str] = {}
    to_process: list[tuple[dict, dict]] = []
    for sig in signatures:
        if sig.get("parse_error") or not sig.get("classes"):
            continue
        source_unchanged = sig["path"] in unchanged_sources
        for cls in sig["classes"]:
            class_id = f"{sig['path']}::{cls['name']}"
            out_path = _class_doc_path(state.output_dir, sig["path"], cls["name"])

            if source_unchanged and out_path.exists():
                # Source hasn't changed since last successful run and the doc
                # is already on disk — no LLM call needed.
                rel = str(out_path.relative_to(state.output_dir))
                written[class_id] = rel
                state.artifact_index[class_id] = rel
                continue
            to_process.append((sig, cls))

    sem = asyncio.Semaphore(max(1, parallelism))

    async def _one(sig: dict, cls: dict) -> tuple[str, str]:
        class_id = f"{sig['path']}::{cls['name']}"
        out_path = _class_doc_path(state.output_dir, sig["path"], cls["name"])

        async with sem:
            doer_prompt = build_class_prompt(
                class_name=cls["name"],
                source_path=sig["path"],
                signature_json=json.dumps(cls, indent=2),
            )

            def checker_prompt_fn(doc: str, *, _cls=cls, _sig=sig) -> str:
                return build_checker_prompt(
                    class_name=_cls["name"],
                    source_path=_sig["path"],
                    doc_markdown=doc,
                )

            result = await doer_checker_loop(
                artifact_id=class_id,
                doer=doer,
                checker=checker,
                doer_prompt=doer_prompt,
                checker_prompt_fn=checker_prompt_fn,
                runner=runner,
                hil_sink=state.hil_issues,
                stage_name=STAGE_NAME,
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            content = result.text
            if result.status == "shipped_with_hil":
                hil_id = state.hil_issues[-1]["id"]
                content = (
                    f"{inline_comment(hil_id, 'doc-quality checker disputed claims')}\n\n" + content
                )
            out_path.write_text(content)
            rel = str(out_path.relative_to(state.output_dir))
            return class_id, rel

    for class_id, rel in await asyncio.gather(*[_one(s, c) for s, c in to_process]):
        written[class_id] = rel
        state.artifact_index[class_id] = rel

    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 4)
    state.save()
    return written


def _unchanged_source_paths(state: PipelineState) -> set[str]:
    """Source paths whose SHA1 matches prev_hashes. Empty if Stage 0 output
    is missing or unreadable — falls back to full regeneration."""
    stage0_path = state.output_dir / STAGE0_FILENAME
    if not stage0_path.exists() or not state.prev_hashes:
        return set()
    try:
        current = json.loads(stage0_path.read_text()).get("hashes") or {}
    except (json.JSONDecodeError, OSError):
        return set()
    return state.unchanged_paths(current)


def _class_doc_path(output_dir: Path, source_path: str, class_name: str) -> Path:
    """Map a source file + class to an output path under packages/<pkg>/classes/."""
    src = Path(source_path)
    # Package = the last directory component of the source path (e.g. "payments")
    # Skip "src" wrappers — they're structural, not semantic packages.
    parts = [p for p in src.parent.parts if p not in ("src", ".", "")]
    pkg = parts[-1] if parts else "root"
    return output_dir / OUTPUT_SUBDIR / pkg / "classes" / f"{class_name}.md"
