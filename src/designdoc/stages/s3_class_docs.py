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
import hashlib
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
from designdoc.io_utils import atomic_write
from designdoc.loop import doer_checker_loop
from designdoc.stages._common import current_source_hashes, unwrap_taskgroup_exception
from designdoc.stages.s1_index import OUTPUT_FILENAME as STAGE1_FILENAME
from designdoc.state import PipelineState, StageStatus, state_lock

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

    source_hashes = current_source_hashes(state)

    to_process: list[tuple[dict, dict]] = []
    for sig in signatures:
        if sig.get("parse_error") or not sig.get("classes"):
            continue
        for cls in sig["classes"]:
            to_process.append((sig, cls))

    sem = asyncio.Semaphore(max(1, parallelism))

    async def _one(sig: dict, cls: dict) -> None:
        class_id = f"{sig['path']}::{cls['name']}"
        out_path = _class_doc_path(state.output_dir, sig["path"], cls["name"])
        current_input_hash = _class_input_hash(
            source_sha=source_hashes.get(sig["path"], ""),
            class_signature=cls,
        )

        # v1.2 within-stage skip: if we already produced this class with
        # the same inputs AND the doc exists on disk, no LLM call.
        prior = state.artifact_index.get(class_id, {})
        if (
            prior.get("input_hash") == current_input_hash
            and current_input_hash != ""
            and out_path.exists()
        ):
            return

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
            content = result.text
            if result.status == "shipped_with_hil":
                hil_id = state.hil_issues[-1]["id"]
                content = (
                    f"{inline_comment(hil_id, 'doc-quality checker disputed claims')}\n\n" + content
                )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(out_path, content)
            rel = str(out_path.relative_to(state.output_dir))

        async with state_lock:
            state.artifact_index[class_id] = {
                "path": rel,
                "input_hash": current_input_hash,
            }
            state.save()

    # TaskGroup cancels siblings on first raise — gather would leak paid
    # LLM calls past a BudgetExceededError. Unwrap to preserve the raw
    # exception type the orchestrator and callers expect.
    try:
        async with asyncio.TaskGroup() as tg:
            for sig, cls in to_process:
                tg.create_task(_one(sig, cls))
    except BaseExceptionGroup as eg:
        raise unwrap_taskgroup_exception(eg) from eg

    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 4)
    async with state_lock:
        state.save()

    # Return the {class_id: path} mapping that downstream stages expect,
    # sourced from artifact_index so skipped-and-processed classes are
    # both represented.
    written: dict[str, str] = {}
    for sig in signatures:
        for cls in sig.get("classes", []):
            class_id = f"{sig['path']}::{cls['name']}"
            entry = state.artifact_index.get(class_id)
            if entry:
                written[class_id] = entry["path"]
    return written


def _class_input_hash(source_sha: str, class_signature: dict) -> str:
    """Per-class input hash: source SHA + canonical JSON of the signature."""
    if not source_sha:
        return ""
    h = hashlib.sha1()
    h.update(source_sha.encode())
    h.update(json.dumps(class_signature, sort_keys=True).encode())
    return h.hexdigest()


def _class_doc_path(output_dir: Path, source_path: str, class_name: str) -> Path:
    """Map a source file + class to an output path under packages/<pkg>/classes/."""
    src = Path(source_path)
    # Package = the last directory component of the source path (e.g. "payments")
    # Skip "src" wrappers — they're structural, not semantic packages.
    parts = [p for p in src.parent.parts if p not in ("src", ".", "")]
    pkg = parts[-1] if parts else "root"
    return output_dir / OUTPUT_SUBDIR / pkg / "classes" / f"{class_name}.md"
