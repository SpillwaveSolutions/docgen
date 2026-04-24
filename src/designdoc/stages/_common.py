"""Shared helpers for pipeline stages.

Module-level utilities that multiple stages need. Kept under `stages/`
rather than on `PipelineState` to avoid growing state's public API with
functions that just read stage-specific output files.
"""

from __future__ import annotations

import json

from designdoc.budget import BudgetExceededError
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


def unwrap_taskgroup_exception(eg: BaseExceptionGroup) -> BaseException:
    """Extract the exception that should propagate out of a TaskGroup.

    Stages wrap per-artifact work in ``asyncio.TaskGroup`` so one child
    raising BudgetExceededError cancels siblings before they consume more
    paid LLM calls. TaskGroup surfaces failures as a ``BaseExceptionGroup``,
    which would break orchestrator.py's ``except BudgetExceededError`` and
    any test that pattern-matches on the raw inner exception.

    Unwrap rules:
    1. Any BudgetExceededError inside the group wins — the first one is
       returned so the orchestrator can halt-and-resume as usual.
    2. A single non-budget leaf exception returns unwrapped — preserves
       pre-TaskGroup behavior for callers matching on the inner type.
    3. Otherwise return the group as-is (multiple unrelated failures).
    """
    budget_group, _ = eg.split(BudgetExceededError)
    if budget_group is not None:
        leaves = list(_iter_leaves(budget_group))
        if leaves:
            return leaves[0]
    leaves = list(_iter_leaves(eg))
    if len(leaves) == 1:
        return leaves[0]
    return eg


def _iter_leaves(eg: BaseExceptionGroup):
    """Depth-first walk yielding non-group leaf exceptions."""
    for exc in eg.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            yield from _iter_leaves(exc)
        else:
            yield exc
