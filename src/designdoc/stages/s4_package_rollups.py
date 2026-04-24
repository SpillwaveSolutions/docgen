"""Stage 4: per-package README rollups.

Reads only the generated class docs from Stage 3 — never source. For each
package dir containing class docs, runs the doer/checker loop to produce a
package README.md at packages/<pkg>/README.md.

v1.1 incremental: SHA1 the concatenation of class docs (sorted by filename)
and compare against state.rollup_hashes["package:<name>"]. On match, the
package README from the previous run is kept untouched and no LLM call is
made. On any change, regenerate and update the recorded hash.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from designdoc.agents.package_documenter import (
    build_checker_prompt,
    build_doer_prompt,
    make_package_doc_checker,
    make_package_documenter,
)
from designdoc.hil import inline_comment
from designdoc.io_utils import atomic_write, sha1_keyed
from designdoc.loop import doer_checker_loop
from designdoc.state import PipelineState, StageStatus, state_lock

STAGE_NAME = "package_rollups"


async def run(
    *,
    state: PipelineState,
    runner,
    doer_model: str = "claude-sonnet-4-6",
    checker_model: str = "claude-sonnet-4-6",
    parallelism: int = 1,
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
    to_process: list[tuple[str, dict[str, str], str, Path]] = []
    for pkg_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        class_docs = _collect_class_docs(pkg_dir)
        if not class_docs:
            continue
        pkg_name = pkg_dir.name
        rollup_key = f"package:{pkg_name}"
        input_hash = sha1_keyed(class_docs)
        readme_path = pkg_dir / "README.md"

        if state.rollup_hashes.get(rollup_key) == input_hash and readme_path.exists():
            written[pkg_name] = str(readme_path.relative_to(state.output_dir))
            continue
        to_process.append((pkg_name, class_docs, input_hash, readme_path))

    sem = asyncio.Semaphore(max(1, parallelism))

    async def _one(pkg_name, class_docs, input_hash, readme_path):
        rollup_key = f"package:{pkg_name}"

        # v1.2 within-stage skip: if artifact_index already records this
        # package with the same input_hash and the README exists, skip.
        prior = state.artifact_index.get(rollup_key, {})
        if prior.get("input_hash") == input_hash and input_hash != "" and readme_path.exists():
            rel = str(readme_path.relative_to(state.output_dir))
            return pkg_name, rel, input_hash

        async with sem:
            doer_prompt = build_doer_prompt(pkg_name, class_docs)

            def checker_prompt_fn(readme: str, *, _pkg=pkg_name, _docs=class_docs) -> str:
                return build_checker_prompt(_pkg, _docs, readme)

            result = await doer_checker_loop(
                artifact_id=rollup_key,
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
                content = f"{inline_comment(hil_id, 'package rollup disputed')}\n\n" + content
            readme_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(readme_path, content)
            rel = str(readme_path.relative_to(state.output_dir))

        async with state_lock:
            state.artifact_index[rollup_key] = {
                "path": rel,
                "input_hash": input_hash,
            }
            # v1.1 cross-run skip coexists with v1.2 within-stage skip
            state.rollup_hashes[rollup_key] = input_hash
            state.save()

        return pkg_name, rel, input_hash

    for pkg_name, rel_path, _input_hash in await asyncio.gather(
        *[_one(*args) for args in to_process]
    ):
        written[pkg_name] = rel_path

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
