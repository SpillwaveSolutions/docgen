"""Orchestrator: iterate the stage table, checkpoint after each, halt on budget.

Rules:
- Stages marked DONE are skipped (resume).
- BudgetExceededError exits cleanly — state is saved with the current stage
  marked FAILED so subsequent `designdoc status` shows where we halted.
- Stage 5 preflight runs mmdc before the pipeline starts; if mmdc is missing
  and Stage 5 is not skipped, halt with a clear error.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from designdoc.budget import BudgetExceededError, CostAccumulator
from designdoc.config import Config
from designdoc.mermaid.mmdc import MmdcNotAvailableError, preflight
from designdoc.stages import (
    s0_discover,
    s1_index,
    s2_file_analysis,
    s3_class_docs,
    s4_package_rollups,
    s5_mermaid,
    s6_tech_debt,
    s7_system_rollup,
    s8_finalize,
)
from designdoc.state import PipelineState, StageStatus

log = logging.getLogger(__name__)

StageFn = Callable[..., Awaitable[Any]]


@dataclass
class StageEntry:
    name: str
    run: StageFn
    needs_runner: bool = True  # Stage 0/1/8 are deterministic


def default_stage_table() -> list[StageEntry]:
    return [
        StageEntry("discover", s0_discover.run, needs_runner=False),
        StageEntry("index", s1_index.run, needs_runner=False),
        StageEntry("file_analysis", s2_file_analysis.run),
        StageEntry("class_docs", s3_class_docs.run),
        StageEntry("package_rollups", s4_package_rollups.run),
        StageEntry("mermaid", s5_mermaid.run),
        StageEntry("tech_debt", s6_tech_debt.run),
        StageEntry("system_rollup", s7_system_rollup.run),
        StageEntry("finalize", s8_finalize.run, needs_runner=False),
    ]


class Orchestrator:
    def __init__(
        self,
        *,
        state: PipelineState,
        runner,
        budget: CostAccumulator,
        config: Config | None = None,
        skip_stages: set[str] | None = None,
        stages: list[StageEntry] | None = None,
    ):
        self.state = state
        self.runner = runner
        self.budget = budget
        self.config = config or Config()
        # Merge CLI skip + config skip — a stage listed in either is skipped.
        self.skip = (skip_stages or set()) | set(self.config.skip_stages)
        all_stages = stages or default_stage_table()
        # [stages].only is an allow-list: if non-empty, ONLY those stages run
        # and every other stage is effectively skipped (regardless of --skip).
        if self.config.only_stages:
            allowed = set(self.config.only_stages)
            self.stages = [s for s in all_stages if s.name in allowed]
        else:
            self.stages = all_stages

    async def run(self) -> None:
        """Run every stage in order, skipping DONE and filtered stages.

        Runs mmdc preflight before the first stage if the mermaid stage is
        enabled — halts early if mmdc is missing.
        """
        if "mermaid" not in self.skip and self.state.stages.get("mermaid") != StageStatus.DONE:
            try:
                preflight()
            except MmdcNotAvailableError:
                log.exception("mmdc preflight failed; pass skip_stages={'mermaid'} or install it")
                raise

        for entry in self.stages:
            if entry.name in self.skip:
                log.info("stage %s skipped by config", entry.name)
                continue
            if self.state.stages.get(entry.name) == StageStatus.DONE:
                log.info("stage %s already done, skipping", entry.name)
                continue
            try:
                kwargs: dict[str, Any] = {"state": self.state}
                if entry.needs_runner:
                    kwargs["runner"] = self.runner
                kwargs.update(self._stage_kwargs(entry.name))
                await entry.run(**kwargs)
                self.budget.save()
            except BudgetExceededError:
                self.state.stages[entry.name] = StageStatus.FAILED
                self.state.save()
                self.budget.save()
                raise

    def _stage_kwargs(self, stage_name: str) -> dict[str, Any]:
        """Per-stage kwargs derived from config. Only pass keys the stage accepts.

        Kept explicit per-stage because the stage signatures diverge —
        s2_file_analysis uses a pydantic schema checker (no checker_model),
        s5_mermaid's models are driven by the mermaid_generator factory
        directly, and s8_finalize is deterministic.
        """
        p = self.config.parallelism
        if stage_name == "discover":
            return {"exclude_paths": list(self.config.exclude_paths)}
        if stage_name == "file_analysis":
            return {"doer_model": self.config.doer_model, "parallelism": p}
        if stage_name in ("class_docs", "package_rollups"):
            return {
                "doer_model": self.config.doer_model,
                "checker_model": self.config.checker_model,
                "parallelism": p,
            }
        if stage_name == "system_rollup":
            # Single-artifact stage — no parallelism benefit.
            return {
                "doer_model": self.config.doer_model,
                "checker_model": self.config.checker_model,
            }
        if stage_name == "tech_debt":
            return {
                "doer_model": self.config.doer_model,
                "checker_model": self.config.checker_model,
                "mcp_servers": _enabled_mcp(self.config),
                "parallelism": p,
            }
        return {}


def _enabled_mcp(config: Config) -> list[str]:
    servers: list[str] = []
    if config.perplexity_mcp:
        servers.append("perplexity")
    if config.context7_mcp:
        servers.append("context7")
    if config.agent_brain_mcp:
        servers.append("agent_brain")
    return servers
