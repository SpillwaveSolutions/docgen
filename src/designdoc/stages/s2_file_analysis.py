"""Stage 2: per-file summaries via file-analyzer + pydantic schema checker.

For each file in Stage 1's signature list, run the doer/schema loop to produce
a validated FileSummary. Results persist to stage2_summaries.json.

v1.2 within-stage resume: each completed file updates state.artifact_index
under state_lock and rewrites stage2_summaries.json atomically. A crash
mid-stage leaves a partial JSON + partial artifact_index; the rerun skips
any file whose input hash still matches.
"""

from __future__ import annotations

import asyncio
import json
import logging

from pydantic import ValidationError

from designdoc.agents.file_analyzer import FileSummary, build_prompt, make_file_analyzer
from designdoc.io_utils import atomic_write
from designdoc.loop import doer_schema_loop
from designdoc.stages._common import current_source_hashes, unwrap_taskgroup_exception
from designdoc.stages.s1_index import OUTPUT_FILENAME as STAGE1_FILENAME
from designdoc.state import PipelineState, StageStatus, state_lock
from designdoc.verdict import extract_json_object

log = logging.getLogger(__name__)

STAGE_NAME = "file_analysis"
OUTPUT_FILENAME = "stage2_summaries.json"


async def run(
    *,
    state: PipelineState,
    runner,
    doer_model: str = "claude-sonnet-4-6",
    parallelism: int = 1,
) -> dict[str, dict]:
    """Execute Stage 2. Returns {relative_path: summary_dict}."""
    stage1_path = state.output_dir / STAGE1_FILENAME
    if not stage1_path.exists():
        raise FileNotFoundError(f"stage 1 output missing ({stage1_path}); run stage 1 first")

    state.stages[STAGE_NAME] = StageStatus.RUNNING
    async with state_lock:
        state.save()

    signatures = json.loads(stage1_path.read_text())
    doer = make_file_analyzer(model=doer_model)

    current_hashes = current_source_hashes(state)
    reusable = _load_reusable_summaries(state, current_hashes)

    # Load any partial summaries from a prior (possibly crashed) run, then
    # drop entries for files that no longer exist in the current signatures —
    # deleted sources must not linger in summaries.
    existing_path = state.output_dir / OUTPUT_FILENAME
    results: dict[str, dict] = {}
    if existing_path.exists():
        try:
            results = json.loads(existing_path.read_text())
        except (json.JSONDecodeError, OSError):
            results = {}
    current_paths = {s["path"] for s in signatures}
    results = {p: v for p, v in results.items() if p in current_paths}

    to_process: list[dict] = []
    for sig in signatures:
        path = sig["path"]
        if sig.get("parse_error"):
            continue
        if path in reusable:
            # v1.1 cross-run skip: source unchanged since last SUCCESSFUL run.
            results[path] = reusable[path]
            continue
        current_hash = current_hashes.get(path, "")
        artifact_id = f"file:{path}"
        prior = state.artifact_index.get(artifact_id, {})
        if prior.get("input_hash") == current_hash and current_hash != "" and path in results:
            # v1.2 within-stage skip: this file was checkpointed in a prior
            # (possibly crashed) run of THIS invocation. Nothing to do.
            continue
        to_process.append(sig)

    sem = asyncio.Semaphore(max(1, parallelism))

    async def _one(sig: dict) -> None:
        path = sig["path"]
        current_hash = current_hashes.get(path, "")
        async with sem:
            prompt = build_prompt(path, json.dumps(sig, indent=2))
            result = await doer_schema_loop(
                artifact_id=f"file:{path}",
                doer=doer,
                doer_prompt=prompt,
                schema_model=FileSummary,
                runner=runner,
                hil_sink=state.hil_issues,
                stage_name=STAGE_NAME,
                state=state,
            )
            summary = _parse_or_placeholder(result.text, path)

        async with state_lock:
            results[path] = summary
            atomic_write(
                state.output_dir / OUTPUT_FILENAME,
                json.dumps(results, indent=2),
            )
            state.artifact_index[f"file:{path}"] = {
                "path": OUTPUT_FILENAME,
                "input_hash": current_hash,
            }
            state.save()

    # TaskGroup cancels siblings on first raise — gather would leak paid
    # LLM calls past a BudgetExceededError. Unwrap to preserve the raw
    # exception type the orchestrator and callers expect.
    try:
        async with asyncio.TaskGroup() as tg:
            for sig in to_process:
                tg.create_task(_one(sig))
    except BaseExceptionGroup as eg:
        raise unwrap_taskgroup_exception(eg) from eg

    # Persist the pruned + updated summaries even when no LLM calls fired
    # (e.g. everything was reusable from prev_hashes and a file was deleted:
    # the on-disk JSON must drop the stale entry).
    async with state_lock:
        atomic_write(
            state.output_dir / OUTPUT_FILENAME,
            json.dumps(results, indent=2),
        )
        state.stages[STAGE_NAME] = StageStatus.DONE
        state.current_stage = max(state.current_stage, 3)
        state.save()
    return results


def _load_reusable_summaries(
    state: PipelineState, current_hashes: dict[str, str]
) -> dict[str, dict]:
    """Cross-run incremental: files whose hash matches prev_hashes AND whose
    prior summary survives in stage2_summaries.json."""
    summaries_path = state.output_dir / OUTPUT_FILENAME
    if not summaries_path.exists() or not state.prev_hashes:
        return {}
    try:
        prev_summaries = json.loads(summaries_path.read_text()) or {}
    except (json.JSONDecodeError, OSError):
        return {}
    unchanged = state.unchanged_paths(current_hashes)
    return {path: prev_summaries[path] for path in unchanged if path in prev_summaries}


def _parse_or_placeholder(text: str, path: str) -> dict:
    placeholder = {
        "purpose": f"(HIL: summary for {path} disputed — see hil-issues.yaml)",
        "key_types": [],
        "key_functions": [],
        "external_deps": [],
        "notes": "unresolved",
    }
    try:
        # Issue #41: same tolerant pre-parse as doer_schema_loop. Without this
        # a successful loop ending on attempt N>1 with fenced/preambled output
        # would still hit the placeholder path here.
        return FileSummary.model_validate_json(extract_json_object(text)).model_dump()
    except ValidationError:
        # Expected: doer output didn't match schema — placeholder is intended.
        return placeholder
    except Exception:
        # Unexpected (encoding, memory, etc.) — log loud (Invariant 4) but
        # still return placeholder so the pipeline doesn't halt on a single file.
        log.exception("_parse_or_placeholder: unexpected exception for %s", path)
        return placeholder
