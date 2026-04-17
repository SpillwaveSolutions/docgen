"""file-analyzer agent: produces a FileSummary JSON for a single source file."""

from __future__ import annotations

from pydantic import BaseModel, Field

from designdoc.agents.prompts import FILE_ANALYZER_SYSTEM
from designdoc.runner import AgentDef


class FileSummary(BaseModel):
    """Structured summary emitted by the file-analyzer doer.

    This IS the checker for Stage 2 — if the doer's output parses into this
    model, the artifact passes. If not, pydantic's ValidationError feeds the
    retry prompt (no LLM check needed).
    """

    purpose: str = Field(min_length=1)
    key_types: list[str] = Field(default_factory=list)
    key_functions: list[str] = Field(default_factory=list)
    external_deps: list[str] = Field(default_factory=list)
    notes: str = ""


def make_file_analyzer(model: str = "claude-sonnet-4-6") -> AgentDef:
    return AgentDef(
        name="file-analyzer",
        system_prompt=FILE_ANALYZER_SYSTEM,
        model=model,
        allowed_tools=["Read", "Grep"],
        max_output_tokens=2048,
    )


def build_prompt(file_path: str, signature_json: str) -> str:
    return (
        f"File path: {file_path}\n\n"
        f"Extracted signature (JSON):\n{signature_json}\n\n"
        "Return the FileSummary JSON as specified in your instructions."
    )
