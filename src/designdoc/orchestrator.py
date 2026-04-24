"""Orchestrator: iterate the stage table, checkpoint after each, halt on budget.

Rules:
- Stages marked DONE are skipped (resume).
- BudgetExceededError is caught and turned into state: stage is marked FAILED,
  state.halted_on_budget is set, state+budget are persisted, and run() returns
  cleanly (does NOT re-raise). The CLI reads the flag to print a resume hint
  and exits 0 (the pipeline is resumable; it's not a crash).
- Stage 5 preflight runs mmdc before the pipeline starts; if mmdc is missing
  and Stage 5 is not skipped, halt with a clear error.
"""

from __future__ import annotations

import logging
import time
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
from designdoc.state import PipelineState, StageStatus, state_lock

log = logging.getLogger(__name__)

StageFn = Callable[..., Awaitable[Any]]


# Other-stage prefix table — used by the class_docs classifier to reject
# artifact ids that already belong to another stage. Module-level so any
# future stage that grows its own prefix can amend it in one place.
_OTHER_PREFIXES = ("file:", "package:", "mermaid:", "dep:", "system:")


def _no_owns_id(_aid: str) -> bool:
    return False


def _no_kwargs(_cfg: Config) -> dict[str, Any]:
    return {}


# Built-in classifier predicates per stage name. StageEntry.__post_init__
# fills owns_id from this table when the constructor caller doesn't supply
# one explicitly — preserves the contract that StageEntry("class_docs",
# fake_fn) classifies "<path>::<Class>" ids without the caller having to
# know the rule.
_BUILTIN_OWNS_ID: dict[str, Callable[[str], bool]] = {
    "file_analysis": lambda aid: aid.startswith("file:"),
    "class_docs": lambda aid: "::" in aid and not aid.startswith(_OTHER_PREFIXES),
    "package_rollups": lambda aid: aid.startswith("package:"),
    "mermaid": lambda aid: aid.startswith("mermaid:"),
    "tech_debt": lambda aid: aid.startswith("dep:"),
    "system_rollup": lambda aid: aid == "system:rollup",
}


# Built-in kwargs builders per stage name. Each stage gets the kwargs its
# `run(**kwargs)` accepts. Lambdas reference _enabled_mcp at module-load
# time but only call it at runtime — forward reference is safe.
_BUILTIN_KWARGS_FN: dict[str, Callable[[Config], dict[str, Any]]] = {
    "discover": lambda cfg: {
        "exclude_paths": list(cfg.exclude_paths),
        "include_languages": list(cfg.include_languages),
    },
    "file_analysis": lambda cfg: {
        "doer_model": cfg.doer_model,
        "parallelism": cfg.parallelism,
    },
    "class_docs": lambda cfg: {
        "doer_model": cfg.doer_model,
        "checker_model": cfg.checker_model,
        "parallelism": cfg.parallelism,
    },
    "package_rollups": lambda cfg: {
        "doer_model": cfg.doer_model,
        "checker_model": cfg.checker_model,
        "parallelism": cfg.parallelism,
    },
    "system_rollup": lambda cfg: {
        "doer_model": cfg.doer_model,
        "checker_model": cfg.checker_model,
    },
    "tech_debt": lambda cfg: {
        "doer_model": cfg.doer_model,
        "checker_model": cfg.checker_model,
        "mcp_servers": _enabled_mcp(cfg),
        "parallelism": cfg.parallelism,
    },
}


@dataclass
class StageEntry:
    name: str
    run: StageFn
    needs_runner: bool = True  # Stage 0/1/8 are deterministic
    owns_id: Callable[[str], bool] | None = None
    kwargs_fn: Callable[[Config], dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        # Fall back to the per-name builtins so casual constructions like
        # StageEntry("class_docs", fake_fn) still get the right classifier
        # without callers having to know the rule.
        if self.owns_id is None:
            self.owns_id = _BUILTIN_OWNS_ID.get(self.name, _no_owns_id)
        if self.kwargs_fn is None:
            self.kwargs_fn = _BUILTIN_KWARGS_FN.get(self.name, _no_kwargs)


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

        total = len(self.stages)
        for idx, entry in enumerate(self.stages, start=1):
            if entry.name in self.skip:
                log.info("[%d/%d] stage %s skipped by config", idx, total, entry.name)
                continue
            if self.state.stages.get(entry.name) == StageStatus.DONE:
                log.info("[%d/%d] stage %s already done, skipping", idx, total, entry.name)
                continue
            prior_count = sum(1 for id_ in self.state.artifact_index if entry.owns_id(id_))
            if prior_count > 0:
                log.info(
                    "[%d/%d] stage %s: %d artifacts checkpointed",
                    idx,
                    total,
                    entry.name,
                    prior_count,
                )
            else:
                log.info("[%d/%d] stage %s starting", idx, total, entry.name)
            start = time.monotonic()
            try:
                kwargs: dict[str, Any] = {"state": self.state}
                if entry.needs_runner:
                    kwargs["runner"] = self.runner
                kwargs.update(entry.kwargs_fn(self.config))
                await entry.run(**kwargs)
                self.budget.save()
            except BudgetExceededError:
                self.state.stages[entry.name] = StageStatus.FAILED
                self.state.halted_on_budget = True
                async with state_lock:
                    self.state.save()
                self.budget.save()
                log.info(
                    "[%d/%d] stage %s halted after %.1fs (budget exceeded) — "
                    "run `designdoc resume --budget <new-cap>` to continue",
                    idx,
                    total,
                    entry.name,
                    time.monotonic() - start,
                )
                return
            log.info(
                "[%d/%d] stage %s done in %.1fs",
                idx,
                total,
                entry.name,
                time.monotonic() - start,
            )


def _enabled_mcp(config: Config) -> list[str]:
    servers: list[str] = []
    if config.perplexity_mcp:
        servers.append("perplexity")
    if config.context7_mcp:
        servers.append("context7")
    if config.agent_brain_mcp:
        servers.append("agent_brain")
    return servers
