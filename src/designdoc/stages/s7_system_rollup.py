"""Stage 7: system + architecture rollup from package READMEs."""

from __future__ import annotations

from designdoc.agents.system_designer import (
    build_checker_prompt,
    build_doer_prompt,
    make_system_checker,
    make_system_designer,
    split_doer_output,
)
from designdoc.hil import inline_comment
from designdoc.loop import doer_checker_loop
from designdoc.state import PipelineState, StageStatus

STAGE_NAME = "system_rollup"
SYSTEM_FILENAME = "SYSTEM_DESIGN.md"
ARCHITECTURE_FILENAME = "ARCHITECTURE.md"


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
    state.save()

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
    )

    sys_md, arch_md = split_doer_output(result.text)
    if result.status == "shipped_with_hil":
        hil_id = state.hil_issues[-1]["id"]
        prefix = f"{inline_comment(hil_id, 'system-design review disputed')}\n\n"
        sys_md = prefix + sys_md
        arch_md = prefix + arch_md

    sys_path = state.output_dir / SYSTEM_FILENAME
    arch_path = state.output_dir / ARCHITECTURE_FILENAME
    sys_path.write_text(sys_md)
    arch_path.write_text(arch_md)

    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 8)
    state.save()
    return {
        SYSTEM_FILENAME: str(sys_path.relative_to(state.output_dir)),
        ARCHITECTURE_FILENAME: str(arch_path.relative_to(state.output_dir)),
    }


def _collect_readmes(packages_dir) -> dict[str, str]:
    return {p.parent.name: p.read_text() for p in sorted(packages_dir.glob("*/README.md"))}
