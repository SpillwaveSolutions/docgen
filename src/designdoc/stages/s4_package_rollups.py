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

import hashlib
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
        rollup_key = f"package:{pkg_name}"
        input_hash = _hash_class_docs(class_docs)
        readme_path = pkg_dir / "README.md"

        # Skip if inputs match the last successful regeneration AND the
        # README is actually on disk (guard against manual deletes).
        if state.rollup_hashes.get(rollup_key) == input_hash and readme_path.exists():
            written[pkg_name] = str(readme_path.relative_to(state.output_dir))
            continue

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

        content = result.text
        if result.status == "shipped_with_hil":
            hil_id = state.hil_issues[-1]["id"]
            content = f"{inline_comment(hil_id, 'package rollup disputed')}\n\n" + content
        readme_path.write_text(content)
        written[pkg_name] = str(readme_path.relative_to(state.output_dir))
        state.rollup_hashes[rollup_key] = input_hash

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


def _hash_class_docs(class_docs: dict[str, str]) -> str:
    """Stable SHA1 over class docs keyed by filename (sorted)."""
    h = hashlib.sha1()
    for name in sorted(class_docs):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(class_docs[name].encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()
