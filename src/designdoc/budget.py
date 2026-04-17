"""Cost accumulation with a hard cap.

Every LLM invocation accrues a UsageRecord through a single CostAccumulator.
When the projected total would exceed cap_usd, we raise BudgetExceededError
BEFORE mutating state — callers that catch the error see prior totals intact.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


class BudgetExceededError(Exception):
    """Raised when an accrual would push total cost past the configured cap."""


@dataclass(slots=True)
class UsageRecord:
    input_tokens: int
    output_tokens: int
    cost_usd: float
    agent: str


@dataclass
class CostAccumulator:
    cap_usd: float
    path: Path | None = None
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    invocations: int = 0
    by_agent: dict[str, float] = field(default_factory=dict)

    def accrue(self, rec: UsageRecord) -> None:
        projected = self.total_cost_usd + rec.cost_usd
        if projected > self.cap_usd:
            raise BudgetExceededError(
                f"budget cap ${self.cap_usd:.2f} would be exceeded "
                f"(current ${self.total_cost_usd:.2f} + next ${rec.cost_usd:.4f} "
                f"= ${projected:.2f})"
            )
        self.total_cost_usd = projected
        self.total_input_tokens += rec.input_tokens
        self.total_output_tokens += rec.output_tokens
        self.invocations += 1
        self.by_agent[rec.agent] = self.by_agent.get(rec.agent, 0.0) + rec.cost_usd

    def save(self) -> None:
        if not self.path:
            return
        data = asdict(self)
        data["path"] = str(self.path)
        self.path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load_or_new(cls, cap_usd: float, path: Path) -> CostAccumulator:
        if path.exists():
            data = json.loads(path.read_text())
            return cls(
                cap_usd=cap_usd,
                path=path,
                total_cost_usd=data["total_cost_usd"],
                total_input_tokens=data["total_input_tokens"],
                total_output_tokens=data["total_output_tokens"],
                invocations=data["invocations"],
                by_agent=data["by_agent"],
            )
        return cls(cap_usd=cap_usd, path=path)
