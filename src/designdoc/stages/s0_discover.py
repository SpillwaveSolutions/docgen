"""Stage 0: discover languages and file tree.

No LLM. Walks the target repo, writes stage0_discovery.json to the output dir,
and marks the discover stage DONE.
"""

from __future__ import annotations

import json

from designdoc.index.discover import DiscoveryReport, discover
from designdoc.io_utils import atomic_write
from designdoc.state import PipelineState, StageStatus, state_lock

STAGE_NAME = "discover"
OUTPUT_FILENAME = "stage0_discovery.json"


async def run(
    *,
    state: PipelineState,
    exclude_paths: list[str] | None = None,
    include_languages: list[str] | None = None,
) -> DiscoveryReport:
    """Execute Stage 0 and checkpoint the result."""
    state.stages[STAGE_NAME] = StageStatus.RUNNING
    async with state_lock:
        state.save()

    report = discover(
        state.target_repo,
        exclude_paths=exclude_paths,
        include_languages=include_languages,
    )

    state.output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(
        state.output_dir / OUTPUT_FILENAME,
        json.dumps(report.to_dict(), indent=2),
    )

    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 1)
    async with state_lock:
        state.save()
    return report
