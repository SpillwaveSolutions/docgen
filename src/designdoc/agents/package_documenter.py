"""package-documenter + package-doc-checker agents.

The documenter reads only the generated class docs (not source). The checker
also sees only the class docs plus the rollup — a narrow, bounded scope that
prevents token blowup on large packages.
"""

from __future__ import annotations

from designdoc.agents.prompts import PACKAGE_DOC_CHECKER_SYSTEM, PACKAGE_DOCUMENTER_SYSTEM
from designdoc.runner import AgentDef


def make_package_documenter(model: str = "claude-sonnet-4-6") -> AgentDef:
    return AgentDef(
        name="package-documenter",
        system_prompt=PACKAGE_DOCUMENTER_SYSTEM,
        model=model,
        allowed_tools=[],  # intentionally no Read — reads only what's in the prompt
        max_output_tokens=3072,
    )


def make_package_doc_checker(model: str = "claude-sonnet-4-6") -> AgentDef:
    return AgentDef(
        name="package-doc-checker",
        system_prompt=PACKAGE_DOC_CHECKER_SYSTEM,
        model=model,
        allowed_tools=[],
        max_output_tokens=2048,
    )


def build_doer_prompt(package_name: str, class_docs: dict[str, str]) -> str:
    blocks = [f"### {name}\n\n{doc}" for name, doc in class_docs.items()]
    return f"Package: {package_name}\n\nClass docs to summarize:\n\n" + "\n\n---\n\n".join(blocks)


def build_checker_prompt(package_name: str, class_docs: dict[str, str], readme: str) -> str:
    blocks = [f"### {name}\n\n{doc}" for name, doc in class_docs.items()]
    return (
        f"Package: {package_name}\n\n"
        f"Class docs (source of truth):\n\n" + "\n\n---\n\n".join(blocks) + "\n\n"
        f"Proposed README to review:\n\n{readme}\n\n"
        "Emit your JSON verdict per the system rules."
    )
