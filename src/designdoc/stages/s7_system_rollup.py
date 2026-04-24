"""Stage 7: system + architecture rollup from package READMEs.

v1.1 incremental: SHA1 the concatenation of package READMEs (sorted by name)
and compare against state.rollup_hashes["system:rollup"]. On match, keep the
existing SYSTEM_DESIGN.md + ARCHITECTURE.md and skip the LLM call.

v1.2 within-stage resume: additionally record a "system:rollup" entry in
state.artifact_index after the doer/checker loop succeeds. Mirrors stages
2-6 so a mid-stage crash preserves the checkpoint and a rerun skips the
loop when both input_hash matches and both outputs still exist.
"""

from __future__ import annotations

from designdoc.agents.system_designer import (
    build_checker_prompt,
    build_doer_prompt,
    make_system_checker,
    make_system_designer,
    split_doer_output,
)
from designdoc.hil import inline_comment
from designdoc.io_utils import atomic_write, sha1_keyed
from designdoc.loop import doer_checker_loop
from designdoc.state import PipelineState, StageStatus, state_lock

STAGE_NAME = "system_rollup"
SYSTEM_FILENAME = "SYSTEM_DESIGN.md"
ARCHITECTURE_FILENAME = "ARCHITECTURE.md"
ROLLUP_KEY = "system:rollup"


async def run(
    *,
    state: PipelineState,
    runner,
    doer_model: str = "claude-sonnet-4-6",
    checker_model: str = "claude-sonnet-4-6",
) -> dict[str, str]:
    """Execute Stage 7. Returns {filename: relative_path}."""
    packages_dir = state.output_dir / "packages"
    if not packages_dir.exists():
        raise FileNotFoundError(f"packages dir missing ({packages_dir})")

    pkg_readmes = _collect_readmes(packages_dir)
    if not pkg_readmes:
        raise FileNotFoundError("no package READMEs found — run Stage 4 first")

    state.stages[STAGE_NAME] = StageStatus.RUNNING
    async with state_lock:
        state.save()

    sys_path = state.output_dir / SYSTEM_FILENAME
    arch_path = state.output_dir / ARCHITECTURE_FILENAME
    input_hash = sha1_keyed(pkg_readmes)

    # Skip the loop when inputs are unchanged AND both outputs still exist.
    # Two caches feed the check: artifact_index (v1.2 within-stage, survives
    # a mid-stage crash) and rollup_hashes (v1.1 cross-run, set only after
    # full success). Either match is sufficient.
    prior_index_hash = state.artifact_index.get(ROLLUP_KEY, {}).get("input_hash")
    cached = input_hash != "" and (
        prior_index_hash == input_hash or state.rollup_hashes.get(ROLLUP_KEY) == input_hash
    )
    if cached and sys_path.exists() and arch_path.exists():
        state.stages[STAGE_NAME] = StageStatus.DONE
        state.current_stage = max(state.current_stage, 8)
        async with state_lock:
            state.save()
        return {
            SYSTEM_FILENAME: str(sys_path.relative_to(state.output_dir)),
            ARCHITECTURE_FILENAME: str(arch_path.relative_to(state.output_dir)),
        }

    doer = make_system_designer(model=doer_model)
    checker = make_system_checker(model=checker_model)
    doer_prompt = build_doer_prompt(pkg_readmes)

    def checker_prompt_fn(combined: str) -> str:
        return build_checker_prompt(pkg_readmes, combined)

    result = await doer_checker_loop(
        artifact_id="system:rollup",
        doer=doer,
        checker=checker,
        doer_prompt=doer_prompt,
        checker_prompt_fn=checker_prompt_fn,
        runner=runner,
        hil_sink=state.hil_issues,
        stage_name=STAGE_NAME,
        state=state,
    )

    sys_md, arch_md = split_doer_output(result.text)
    if result.status == "shipped_with_hil":
        hil_id = state.hil_issues[-1]["id"]
        prefix = f"{inline_comment(hil_id, 'system-design review disputed')}\n\n"
        sys_md = prefix + sys_md
        arch_md = prefix + arch_md

    atomic_write(sys_path, sys_md)
    atomic_write(arch_path, arch_md)

    # v1.2 within-stage checkpoint: record system:rollup in artifact_index
    # so a rerun after a mid-stage crash can skip the loop. Mirrors the
    # pattern used by stages 2-6. rollup_hashes stays as the cross-run cache.
    state.artifact_index[ROLLUP_KEY] = {
        "path": str(sys_path.relative_to(state.output_dir)),
        "input_hash": input_hash,
    }
    state.rollup_hashes[ROLLUP_KEY] = input_hash
    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 8)
    async with state_lock:
        state.save()
    return {
        SYSTEM_FILENAME: str(sys_path.relative_to(state.output_dir)),
        ARCHITECTURE_FILENAME: str(arch_path.relative_to(state.output_dir)),
    }


def _collect_readmes(packages_dir) -> dict[str, str]:
    return {p.parent.name: p.read_text() for p in sorted(packages_dir.glob("*/README.md"))}
