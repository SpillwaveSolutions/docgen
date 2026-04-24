"""Stage 6: per-dependency tech-debt research + cross-reference check.

Reads dependency manifests, runs doer/checker loop per dep (both agents have
Perplexity + Context7 MCP access), and emits TECH_DEBT.md with one row per dep.

v1.1 incremental: hash the stable (name, pinned, source) triple set of
parsed deps. Skip the whole stage if state.rollup_hashes["tech_debt"]
matches AND TECH_DEBT.md is still on disk.

v1.2 within-stage checkpoint: after each dep is researched, persist
artifact_index["dep:<name>"] with the per-dep input_hash and serialised row.
On resume, any dep whose entry matches is skipped (zero LLM calls). The
partial TECH_DEBT.md is rewritten atomically after every completed dep so a
mid-stage crash leaves a consistent (partial) ledger on disk.
"""

from __future__ import annotations

import asyncio
import json

from designdoc.agents.tech_debt import (
    build_crossref_prompt,
    build_researcher_prompt,
    make_tech_debt_crossref,
    make_tech_debt_researcher,
)
from designdoc.index.manifests import Dep, parse_manifests
from designdoc.io_utils import atomic_write, sha1_keyed
from designdoc.loop import doer_checker_loop
from designdoc.stages._common import unwrap_taskgroup_exception
from designdoc.state import PipelineState, StageStatus, state_lock

STAGE_NAME = "tech_debt"
OUTPUT_FILENAME = "TECH_DEBT.md"
ROLLUP_KEY = "tech_debt"


def _dep_hash_items(deps: list[Dep]) -> dict[str, str]:
    """Encode deps as a dict keyed by name whose value packs ``pinned\\0source``.

    When fed into :func:`designdoc.io_utils.sha1_keyed`, the resulting digest
    absorbs ``name\\0pinned\\0source\\n`` for each dep in sorted(name) order —
    byte-identical to the pre-refactor ``_hash_deps`` / ``_hash_dep`` helpers,
    so existing state.json/artifact_index entries remain valid.
    """
    return {dep.name: f"{dep.pinned}\0{dep.source}" for dep in deps}


async def run(
    *,
    state: PipelineState,
    runner,
    doer_model: str = "claude-sonnet-4-6",
    checker_model: str = "claude-sonnet-4-6",
    mcp_servers: list[str] | None = None,
    parallelism: int = 1,
) -> list[dict]:
    """Execute Stage 6. Returns the list of tech-debt entries (one per dep)."""
    state.stages[STAGE_NAME] = StageStatus.RUNNING
    state.save()

    deps = parse_manifests(state.target_repo)
    input_hash = sha1_keyed(_dep_hash_items(deps))
    output_path = state.output_dir / OUTPUT_FILENAME

    # v1.1 cross-run skip: whole-stage skip when dep manifest is unchanged.
    if state.rollup_hashes.get(ROLLUP_KEY) == input_hash and output_path.exists():
        state.stages[STAGE_NAME] = StageStatus.DONE
        state.current_stage = max(state.current_stage, 7)
        state.save()
        return []

    researcher = make_tech_debt_researcher(model=doer_model, mcp_servers=mcp_servers)
    crossref = make_tech_debt_crossref(model=checker_model, mcp_servers=mcp_servers)

    sem = asyncio.Semaphore(max(1, parallelism))

    # Pre-populate rows from any artifact_index entries written by a prior
    # (possibly crashed) run of this stage.  Key = dep name; value = row dict.
    # The skip gate requires both a matching input_hash AND the output file
    # to exist — if TECH_DEBT.md was deleted the checkpoint is stale.
    rows: dict[str, dict] = {}
    for dep in deps:
        dep_input_hash = sha1_keyed(_dep_hash_items([dep]))
        artifact_id = f"dep:{dep.name}"
        prior = state.artifact_index.get(artifact_id, {})
        if (
            prior.get("input_hash") == dep_input_hash
            and dep_input_hash != ""
            and output_path.exists()
        ):
            stored_row = prior.get("row")
            if stored_row:
                try:
                    rows[dep.name] = json.loads(stored_row)
                    continue
                except (json.JSONDecodeError, TypeError):
                    pass
        # Not yet checkpointed — will be processed below.

    to_process = [dep for dep in deps if dep.name not in rows]

    async def _one(dep: Dep) -> None:
        dep_input_hash = sha1_keyed(_dep_hash_items([dep]))
        async with sem:
            doer_prompt = build_researcher_prompt(dep.name, dep.pinned)

            def checker_prompt_fn(research_json: str, *, _name=dep.name, _pin=dep.pinned) -> str:
                return build_crossref_prompt(_name, _pin, research_json)

            result = await doer_checker_loop(
                artifact_id=f"dep:{dep.name}",
                doer=researcher,
                checker=crossref,
                doer_prompt=doer_prompt,
                checker_prompt_fn=checker_prompt_fn,
                runner=runner,
                hil_sink=state.hil_issues,
                stage_name=STAGE_NAME,
            )
            row = _parse_report(result.text, dep, disputed=result.status != "pass")

        async with state_lock:
            rows[dep.name] = row
            # Rewrite partial ledger atomically so a crash leaves a consistent file.
            ordered_rows = [rows[d.name] for d in deps if d.name in rows]
            atomic_write(output_path, _render_markdown(ordered_rows))
            # v1.2 within-stage checkpoint: persist per-dep entry with row data.
            state.artifact_index[f"dep:{dep.name}"] = {
                "path": OUTPUT_FILENAME,
                "input_hash": dep_input_hash,
                "row": json.dumps(row),
            }
            state.save()

    # TaskGroup cancels siblings on first raise — gather would leak paid
    # LLM calls past a BudgetExceededError. Unwrap to preserve the raw
    # exception type the orchestrator and callers expect.
    try:
        async with asyncio.TaskGroup() as tg:
            for dep in to_process:
                tg.create_task(_one(dep))
    except BaseExceptionGroup as eg:
        raise unwrap_taskgroup_exception(eg) from eg

    # Final atomic write with all deps in manifest order.
    final_rows = [rows[dep.name] for dep in deps if dep.name in rows]
    async with state_lock:
        atomic_write(output_path, _render_markdown(final_rows))
        # v1.1 cross-run skip coexists with v1.2 within-stage checkpoint.
        state.rollup_hashes[ROLLUP_KEY] = input_hash
        state.stages[STAGE_NAME] = StageStatus.DONE
        state.current_stage = max(state.current_stage, 7)
        state.save()
    return final_rows


def _parse_report(text: str, dep, disputed: bool) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"name": dep.name, "pinned": dep.pinned, "status": "unknown"}
    data.setdefault("name", dep.name)
    data.setdefault("pinned", dep.pinned)
    data.setdefault("latest", "unknown")
    data.setdefault("status", "unknown")
    data.setdefault("recommended_action", "none")
    data.setdefault("sources", [])
    data["disputed"] = disputed
    data["source_file"] = dep.source
    return data


def _render_markdown(rows: list[dict]) -> str:
    header = (
        "# Tech Debt Ledger\n\n"
        "Generated by designdoc Stage 6. Each row reflects a researcher + cross-ref\n"
        "checker pair; disputed rows are flagged in the Notes column.\n\n"
        "| Dep | Pinned | Latest | Status | Action | Source | Notes |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    body = "\n".join(
        "| {name} | {pinned} | {latest} | {status} | {action} | {source_file} | {notes} |".format(
            name=r["name"],
            pinned=r["pinned"] or "-",
            latest=r["latest"],
            status=r["status"],
            action=r["recommended_action"],
            source_file=r["source_file"],
            notes=("DISPUTED — see hil-issues.yaml" if r.get("disputed") else ""),
        )
        for r in rows
    )
    return header + body + "\n"
