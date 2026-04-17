"""Resumable pipeline state.

Every stage transition checkpoints to <output_dir>/.designdoc-state.json. On
restart, the orchestrator skips any stage marked DONE — that's what makes a
crashed run picks-up-where-it-stopped.
"""
from __future__ import annotations

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
    artifact_index: dict[str, str] = field(default_factory=dict)

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
                artifact_index=d["artifact_index"],
            )
        return cls(target_repo=target_repo, output_dir=output_dir)
