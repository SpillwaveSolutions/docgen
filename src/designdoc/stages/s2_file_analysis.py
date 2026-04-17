"""Stage 2: per-file summaries via file-analyzer + pydantic schema checker.

For each file in Stage 1's signature list, run the doer/schema loop to produce
a validated FileSummary. Results persist to stage2_summaries.json.

The "checker" here is NOT an LLM — it's pydantic schema validation. Gen 3
principle: when a deterministic check suffices, use it; don't burn a second
API call on a regex-shaped problem.

v1.1 incremental behavior: when state.prev_hashes seeds a current hash map
and a previous stage2_summaries.json exists, files whose SHA1 is unchanged
since the last successful run reuse their existing summary — zero LLM calls.
"""

from __future__ import annotations

import json

from designdoc.agents.file_analyzer import FileSummary, build_prompt, make_file_analyzer
from designdoc.loop import doer_schema_loop
from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.stages.s1_index import OUTPUT_FILENAME as STAGE1_FILENAME
from designdoc.state import PipelineState, StageStatus

STAGE_NAME = "file_analysis"
OUTPUT_FILENAME = "stage2_summaries.json"


async def run(
    *, state: PipelineState, runner, doer_model: str = "claude-sonnet-4-6"
) -> dict[str, dict]:
    """Execute Stage 2. Returns {relative_path: summary_dict}."""
    stage1_path = state.output_dir / STAGE1_FILENAME
    if not stage1_path.exists():
        raise FileNotFoundError(f"stage 1 output missing ({stage1_path}); run stage 1 first")

    state.stages[STAGE_NAME] = StageStatus.RUNNING
    state.save()

    signatures = json.loads(stage1_path.read_text())
    doer = make_file_analyzer(model=doer_model)

    reusable = _load_reusable_summaries(state)

    results: dict[str, dict] = {}
    for sig in signatures:
        path = sig["path"]
        # Skip files that failed to parse in Stage 1 — we have nothing to summarize
        if sig.get("parse_error"):
            continue

        if path in reusable:
            # Unchanged file + previous summary exists — no LLM call needed.
            results[path] = reusable[path]
            continue

        prompt = build_prompt(path, json.dumps(sig, indent=2))
        result = await doer_schema_loop(
            artifact_id=f"file:{path}",
            doer=doer,
            doer_prompt=prompt,
            schema_model=FileSummary,
            runner=runner,
            hil_sink=state.hil_issues,
            stage_name=STAGE_NAME,
        )
        # Persist whatever shipped — valid FileSummary JSON or the raw doer
        # output on HIL path. Downstream stages must tolerate both shapes.
        results[path] = _parse_or_placeholder(result.text, path)

    (state.output_dir / OUTPUT_FILENAME).write_text(json.dumps(results, indent=2))
    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 3)
    state.save()
    return results


def _load_reusable_summaries(state: PipelineState) -> dict[str, dict]:
    """Return {path: summary_dict} for files whose previous summary can be
    reused on this run.

    Eligible when all three hold:
      1. state.prev_hashes has the path (seen on a prior run).
      2. Current Stage 0 discovery hashes that path the same.
      3. The previous stage2_summaries.json still has the entry.

    Any failure to load the upstream JSON -> empty dict (safe default: regenerate).
    """
    stage0_path = state.output_dir / STAGE0_FILENAME
    summaries_path = state.output_dir / OUTPUT_FILENAME
    if not stage0_path.exists() or not summaries_path.exists():
        return {}
    if not state.prev_hashes:
        return {}

    try:
        current_hashes = json.loads(stage0_path.read_text()).get("hashes") or {}
        prev_summaries = json.loads(summaries_path.read_text()) or {}
    except (json.JSONDecodeError, OSError):
        return {}

    unchanged = state.unchanged_paths(current_hashes)
    return {path: prev_summaries[path] for path in unchanged if path in prev_summaries}


def _parse_or_placeholder(text: str, path: str) -> dict:
    """Try to parse as FileSummary; fall back to a placeholder that marks HIL."""
    try:
        return FileSummary.model_validate_json(text).model_dump()
    except Exception:
        return {
            "purpose": f"(HIL: summary for {path} disputed — see hil-issues.yaml)",
            "key_types": [],
            "key_functions": [],
            "external_deps": [],
            "notes": "unresolved",
        }
