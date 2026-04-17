"""Stage 4: per-package README rollups.

Reads only the generated class docs from Stage 3 — never source. For each
package dir containing class docs, runs the doer/checker loop to produce a
package README.md at packages/<pkg>/README.md.
"""

from __future__ import annotations

from pathlib import Path

from designdoc.agents.package_documenter import (
    build_checker_prompt,
    build_doer_prompt,
    make_package_doc_checker,
    make_package_documenter,
)
from designdoc.hil import inline_comment
from designdoc.loop import doer_checker_loop
from designdoc.state import PipelineState, StageStatus

STAGE_NAME = "package_rollups"


async def run(
    *,
    state: PipelineState,
    runner,
    doer_model: str = "claude-sonnet-4-6",
    checker_model: str = "claude-sonnet-4-6",
) -> dict[str, str]:
    """Execute Stage 4. Returns {package_name: readme_path}."""
    packages_dir = state.output_dir / "packages"
    if not packages_dir.exists():
        raise FileNotFoundError(f"packages dir missing ({packages_dir}); run stage 3 first")

    state.stages[STAGE_NAME] = StageStatus.RUNNING
    state.save()

    doer = make_package_documenter(model=doer_model)
    checker = make_package_doc_checker(model=checker_model)

    written: dict[str, str] = {}
    for pkg_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        class_docs = _collect_class_docs(pkg_dir)
        if not class_docs:
            continue
        pkg_name = pkg_dir.name

        doer_prompt = build_doer_prompt(pkg_name, class_docs)

        def checker_prompt_fn(readme: str, *, _pkg=pkg_name, _docs=class_docs) -> str:
            return build_checker_prompt(_pkg, _docs, readme)

        result = await doer_checker_loop(
            artifact_id=f"package:{pkg_name}",
            doer=doer,
            checker=checker,
            doer_prompt=doer_prompt,
            checker_prompt_fn=checker_prompt_fn,
            runner=runner,
            hil_sink=state.hil_issues,
            stage_name=STAGE_NAME,
        )

        readme_path = pkg_dir / "README.md"
        content = result.text
        if result.status == "shipped_with_hil":
            hil_id = state.hil_issues[-1]["id"]
            content = f"{inline_comment(hil_id, 'package rollup disputed')}\n\n" + content
        readme_path.write_text(content)
        written[pkg_name] = str(readme_path.relative_to(state.output_dir))

    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 5)
    state.save()
    return written


def _collect_class_docs(pkg_dir: Path) -> dict[str, str]:
    classes_dir = pkg_dir / "classes"
    if not classes_dir.exists():
        return {}
    return {
        p.stem: p.read_text()
        for p in sorted(classes_dir.glob("*.md"))
        if not p.name.startswith(".")
    }
