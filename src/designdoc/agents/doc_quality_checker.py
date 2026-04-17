"""doc-quality-checker agent: verifies generated docs against source."""

from __future__ import annotations

from designdoc.agents.prompts import DOC_QUALITY_CHECKER_SYSTEM
from designdoc.runner import AgentDef


def make_doc_quality_checker(model: str = "claude-sonnet-4-6") -> AgentDef:
    return AgentDef(
        name="doc-quality-checker",
        system_prompt=DOC_QUALITY_CHECKER_SYSTEM,
        model=model,
        allowed_tools=["Read", "Grep"],
        max_output_tokens=2048,
    )


def build_prompt(class_name: str, source_path: str, doc_markdown: str) -> str:
    return (
        f"Class: {class_name}\n"
        f"Source file: {source_path}\n\n"
        f"Generated doc to review:\n\n{doc_markdown}\n\n"
        "Read the source file and emit your JSON verdict per the system rules."
    )
