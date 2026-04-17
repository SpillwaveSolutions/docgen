"""class-documenter agent: produces a markdown class doc."""

from __future__ import annotations

from designdoc.agents.prompts import CLASS_DOCUMENTER_SYSTEM
from designdoc.runner import AgentDef


def make_class_documenter(model: str = "claude-sonnet-4-6") -> AgentDef:
    return AgentDef(
        name="class-documenter",
        system_prompt=CLASS_DOCUMENTER_SYSTEM,
        model=model,
        allowed_tools=["Read", "Grep"],
        max_output_tokens=4096,
    )


def build_prompt(class_name: str, source_path: str, signature_json: str) -> str:
    return (
        f"Class: {class_name}\n"
        f"Source file: {source_path}\n\n"
        f"Extracted signature (JSON):\n{signature_json}\n\n"
        "Read the source file and produce the markdown class document described "
        "in your instructions."
    )
