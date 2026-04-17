"""system-designer + system-checker agents for Stage 7 rollup."""

from __future__ import annotations

from designdoc.agents.prompts import SYSTEM_CHECKER_SYSTEM, SYSTEM_DESIGNER_SYSTEM
from designdoc.runner import AgentDef


def make_system_designer(model: str = "claude-sonnet-4-6") -> AgentDef:
    return AgentDef(
        name="system-designer",
        system_prompt=SYSTEM_DESIGNER_SYSTEM,
        model=model,
        allowed_tools=[],  # reads only package READMEs in the prompt
        max_output_tokens=6144,
    )


def make_system_checker(model: str = "claude-sonnet-4-6") -> AgentDef:
    return AgentDef(
        name="system-checker",
        system_prompt=SYSTEM_CHECKER_SYSTEM,
        model=model,
        allowed_tools=[],
        max_output_tokens=2048,
    )


def build_doer_prompt(pkg_readmes: dict[str, str]) -> str:
    blocks = [f"### package: {name}\n\n{readme}" for name, readme in pkg_readmes.items()]
    return "Package READMEs:\n\n" + "\n\n---\n\n".join(blocks)


def build_checker_prompt(pkg_readmes: dict[str, str], combined_docs: str) -> str:
    blocks = [f"### package: {name}\n\n{readme}" for name, readme in pkg_readmes.items()]
    return (
        "Package READMEs (source of truth):\n\n"
        + "\n\n---\n\n".join(blocks)
        + "\n\n"
        + "Proposed system + architecture docs:\n\n"
        + combined_docs
        + "\n\n"
        + "Emit your JSON verdict per the system rules."
    )


SYSTEM_MARKER = "<<<SYSTEM_DESIGN>>>"
ARCHITECTURE_MARKER = "<<<ARCHITECTURE>>>"


def split_doer_output(text: str) -> tuple[str, str]:
    """Split the doer's combined output into (system_md, arch_md).

    If the markers are missing, we split at the first occurrence of
    "## Containers" as a last-ditch heuristic, or put everything in system_md.
    """
    if SYSTEM_MARKER in text and ARCHITECTURE_MARKER in text:
        after_system = text.split(SYSTEM_MARKER, 1)[1]
        sys_md, arch_md = after_system.split(ARCHITECTURE_MARKER, 1)
        return sys_md.strip(), arch_md.strip()

    # Heuristic fallback — still produces two files so Stage 8 always finds them
    if "## Containers" in text:
        sys_md, arch_md = text.split("## Containers", 1)
        return sys_md.strip(), "## Containers" + arch_md
    return (
        text.strip(),
        "# Architecture\n\n(HIL: architecture section missing — see hil-issues.yaml)",
    )
