"""Shared helpers for pipeline stages.

Module-level utilities that multiple stages need. Kept under `stages/`
rather than on `PipelineState` to avoid growing state's public API with
functions that just read stage-specific output files.
"""

from __future__ import annotations

import json

from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.state import PipelineState


def current_source_hashes(state: PipelineState) -> dict[str, str]:
    """Load {path: sha} from stage0_discovery.json; empty dict on any failure."""
    stage0_path = state.output_dir / STAGE0_FILENAME
    if not stage0_path.exists():
        return {}
    try:
        return json.loads(stage0_path.read_text()).get("hashes") or {}
    except (json.JSONDecodeError, OSError):
        return {}
