"""Stage 1: AST-lite signatures.

Reads Stage 0's discovery tree, extracts one FileSignature per included file,
and writes the combined result to stage1_signatures.json. No LLM.
"""

from __future__ import annotations

import json

from designdoc.index.signatures import FileSignature, extract_signature
from designdoc.io_utils import atomic_write
from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.state import PipelineState, StageStatus, state_lock

STAGE_NAME = "index"
OUTPUT_FILENAME = "stage1_signatures.json"


async def run(*, state: PipelineState) -> list[FileSignature]:
    """Execute Stage 1 and checkpoint the result."""
    stage0_path = state.output_dir / STAGE0_FILENAME
    if not stage0_path.exists():
        raise FileNotFoundError(f"stage 0 output missing ({stage0_path}); run stage 0 first")

    state.stages[STAGE_NAME] = StageStatus.RUNNING
    async with state_lock:
        state.save()

    data = json.loads(stage0_path.read_text())
    repo_root = state.target_repo
    signatures: list[FileSignature] = []
    for rel in data["tree"]:
        abs_path = repo_root / rel
        if not abs_path.exists():
            continue
        try:
            signatures.append(extract_signature(abs_path, repo_root=repo_root))
        except ValueError:
            # Unsupported extensions are already filtered by discover; anything
            # landing here is a rare race. Skip rather than halt the pipeline.
            continue

    out_path = state.output_dir / OUTPUT_FILENAME
    atomic_write(out_path, json.dumps([s.to_dict() for s in signatures], indent=2))

    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 2)
    async with state_lock:
        state.save()
    return signatures
