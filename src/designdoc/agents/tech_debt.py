"""tech-debt-researcher + tech-debt-crossref-checker agents."""

from __future__ import annotations

from designdoc.agents.prompts import TECH_DEBT_CROSSREF_SYSTEM, TECH_DEBT_RESEARCHER_SYSTEM
from designdoc.runner import AgentDef

_DEFAULT_TECHDEBT_MCP: list[str] = ["perplexity", "context7"]


def _resolve_mcp(mcp_servers: list[str] | None) -> list[str]:
    """None = caller didn't specify, use defaults. `[]` = caller explicitly
    wants no servers. Any other list passes through as-is."""
    if mcp_servers is None:
        return list(_DEFAULT_TECHDEBT_MCP)
    return list(mcp_servers)


def make_tech_debt_researcher(
    model: str = "claude-sonnet-4-6", mcp_servers: list[str] | None = None
) -> AgentDef:
    return AgentDef(
        name="tech-debt-researcher",
        system_prompt=TECH_DEBT_RESEARCHER_SYSTEM,
        model=model,
        allowed_tools=[],
        max_output_tokens=1024,
        mcp_servers=_resolve_mcp(mcp_servers),
    )


def make_tech_debt_crossref(
    model: str = "claude-sonnet-4-6", mcp_servers: list[str] | None = None
) -> AgentDef:
    return AgentDef(
        name="tech-debt-crossref",
        system_prompt=TECH_DEBT_CROSSREF_SYSTEM,
        model=model,
        allowed_tools=[],
        max_output_tokens=1024,
        mcp_servers=_resolve_mcp(mcp_servers),
    )


def build_researcher_prompt(name: str, pinned: str) -> str:
    return (
        f"Dependency: {name}\n"
        f"Pinned version: {pinned}\n\n"
        "Produce the tech-debt JSON report as specified in your instructions."
    )


def build_crossref_prompt(name: str, pinned: str, researcher_json: str) -> str:
    return (
        f"Dependency: {name}\n"
        f"Pinned version: {pinned}\n\n"
        f"Researcher's report:\n{researcher_json}\n\n"
        "Independently verify and emit your JSON verdict."
    )
