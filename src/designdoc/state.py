"""Resumable pipeline state.

Every stage transition checkpoints to <output_dir>/.designdoc-state.json. On
restart, the orchestrator skips any stage marked DONE — that's what makes a
crashed run picks-up-where-it-stopped.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


STATE_FILENAME = ".designdoc-state.json"


@dataclass
class PipelineState:
    target_repo: Path
    output_dir: Path
    current_stage: int = 0
    stages: dict[str, StageStatus] = field(default_factory=dict)
    total_retries: int = 0
    hil_issues: list[dict] = field(default_factory=list)
    # v1.2: each artifact_index entry carries the output path AND the SHA1
    # of its inputs. On resume, an artifact is skipped only if the current
    # input_hash matches the recorded one AND the output file exists.
    artifact_index: dict[str, dict[str, str]] = field(default_factory=dict)
    # prev_hashes: SHA1 map from the last SUCCESSFUL run (seeded by Stage 8).
    # Incremental stages compare current Stage-0 hashes against this to
    # decide which source files need re-analysis.
    prev_hashes: dict[str, str] = field(default_factory=dict)
    # rollup_hashes: per-artifact SHA1 of a stage's INPUTS, keyed by
    # artifact_id. Legacy v1.1 structure retained for cross-run skip logic
    # outside of artifact_index (e.g. stage7 system rollup).
    rollup_hashes: dict[str, str] = field(default_factory=dict)

    @property
    def state_path(self) -> Path:
        return self.output_dir / STATE_FILENAME

    def save(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["target_repo"] = str(self.target_repo)
        data["output_dir"] = str(self.output_dir)
        data["stages"] = {k: str(v) for k, v in self.stages.items()}
        self.state_path.write_text(json.dumps(data, indent=2))

    def unchanged_paths(self, current_hashes: dict[str, str]) -> set[str]:
        """Return relative paths whose current hash matches prev_hashes."""
        return {
            path
            for path, current_hash in current_hashes.items()
            if self.prev_hashes.get(path) == current_hash
        }

    @classmethod
    def load_or_new(cls, output_dir: Path, target_repo: Path) -> PipelineState:
        path = output_dir / STATE_FILENAME
        if path.exists():
            d = json.loads(path.read_text())
            return cls(
                target_repo=Path(d["target_repo"]),
                output_dir=Path(d["output_dir"]),
                current_stage=d["current_stage"],
                stages={k: StageStatus(v) for k, v in d["stages"].items()},
                total_retries=d["total_retries"],
                hil_issues=d["hil_issues"],
                artifact_index=_migrate_artifact_index(d.get("artifact_index", {})),
                prev_hashes=d.get("prev_hashes", {}),
                rollup_hashes=d.get("rollup_hashes", {}),
            )
        return cls(target_repo=target_repo, output_dir=output_dir)


def _migrate_artifact_index(raw: dict) -> dict[str, dict[str, str]]:
    """v1.1 stored values as strings; v1.2 stores dicts with path+input_hash.

    Empty input_hash never matches a real SHA1 -> stage reprocesses, which
    is the same as old behavior. Safe migration, no data loss."""
    migrated: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            migrated[key] = {"path": value, "input_hash": ""}
        else:
            migrated[key] = dict(value)
    return migrated


# Module-level lock so concurrent asyncio gather-children serialize their
# JSON rewrites (not their LLM calls). Acquired ONLY around save().
state_lock = asyncio.Lock()
