"""mermaid-generator agent + mermaid-validator (LLM semantic checker)."""

from __future__ import annotations

from designdoc.agents.prompts import MERMAID_GENERATOR_SYSTEM, MERMAID_VALIDATOR_SYSTEM
from designdoc.runner import AgentDef


def make_mermaid_generator(model: str = "claude-sonnet-4-6") -> AgentDef:
    return AgentDef(
        name="mermaid-generator",
        system_prompt=MERMAID_GENERATOR_SYSTEM,
        model=model,
        allowed_tools=[],
        max_output_tokens=1024,
    )


def make_mermaid_validator(model: str = "claude-sonnet-4-6") -> AgentDef:
    return AgentDef(
        name="mermaid-validator",
        system_prompt=MERMAID_VALIDATOR_SYSTEM,
        model=model,
        allowed_tools=[],
        max_output_tokens=1024,
    )


def build_doer_prompt(artifact_name: str, artifact_text: str) -> str:
    return (
        f"Artifact: {artifact_name}\n\n"
        f"{artifact_text}\n\n"
        "Produce the mermaid diagram as specified. Return only mermaid source."
    )


def build_validator_prompt(artifact_name: str, artifact_text: str, mermaid_src: str) -> str:
    return (
        f"Artifact: {artifact_name}\n\n"
        f"Source artifact:\n{artifact_text}\n\n"
        f"Mermaid diagram (already passed syntax check):\n```mermaid\n{mermaid_src}\n```\n\n"
        "Emit your JSON verdict per the system rules."
    )
