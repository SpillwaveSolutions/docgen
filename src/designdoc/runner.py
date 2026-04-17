"""ClaudeSDKRunner — the ONE place we call claude_agent_sdk.query().

Centralizes:
- Cost accrual (via CostAccumulator)
- Option normalization (AgentDef -> ClaudeAgentOptions)
- Stream collection (messages -> single RunResult)

Agents never call claude_agent_sdk directly; they always go through here.
Tests inject a fake `sdk` so unit tests run offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from designdoc.budget import CostAccumulator, UsageRecord


@dataclass
class AgentDef:
    name: str
    system_prompt: str
    model: str
    allowed_tools: list[str] = field(default_factory=list)
    max_output_tokens: int = 4096
    mcp_servers: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class _SDKProtocol(Protocol):
    async def query(self, *, prompt: str, options: dict) -> dict: ...


class ClaudeSDKRunner:
    def __init__(self, budget: CostAccumulator, sdk: _SDKProtocol | None = None):
        self.budget = budget
        self.sdk = sdk if sdk is not None else _DefaultSDK()

    async def run(self, agent: AgentDef, prompt: str) -> RunResult:
        options = _build_options(agent)
        resp = await self.sdk.query(prompt=prompt, options=options)
        usage = resp.get("usage", {}) or {}
        rec = UsageRecord(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cost_usd=usage.get("cost_usd", 0.0),
            agent=agent.name,
        )
        self.budget.accrue(rec)  # raises BudgetExceededError before mutating state
        return RunResult(
            text=resp.get("text", ""),
            input_tokens=rec.input_tokens,
            output_tokens=rec.output_tokens,
            cost_usd=rec.cost_usd,
        )


def _build_options(agent: AgentDef) -> dict:
    """Translate AgentDef into the options dict passed to the SDK.

    When agent.mcp_servers is non-empty:
    - Adds `mcp__<server>__*` entries to allowed_tools so the agent can
      actually invoke each server's tools (the SDK doesn't auto-allow).
    - Adds setting_sources=["user", "project", "local"] so MCP server
      configs declared in ~/.claude.json, .mcp.json, or project local
      flow through without us having to hand-wire each server's transport.
    """
    tools = list(agent.allowed_tools)
    extras: dict = {}
    if agent.mcp_servers:
        for server in agent.mcp_servers:
            tools.append(f"mcp__{server}__*")
        extras["setting_sources"] = ["user", "project", "local"]

    return {
        "system_prompt": agent.system_prompt,
        "model": agent.model,
        "allowed_tools": tools,
        "mcp_servers": list(agent.mcp_servers),
        **extras,
    }


class _DefaultSDK:
    """Thin adapter over claude_agent_sdk.query(). Imported lazily so unit tests
    that use a FakeSDK don't require the real package to be importable at module load."""

    async def query(self, *, prompt: str, options: dict) -> dict:
        from claude_agent_sdk import (  # noqa: PLC0415 — lazy on purpose
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        extra: dict = {}
        if options.get("setting_sources"):
            extra["setting_sources"] = options["setting_sources"]
        # MCP server dict is built from inherited settings — we don't pass
        # explicit configs here; agent names flow via allowed_tools patterns.
        opts = ClaudeAgentOptions(
            system_prompt=options.get("system_prompt"),
            model=options.get("model"),
            allowed_tools=options.get("allowed_tools") or [],
            **extra,
        )

        text_parts: list[str] = []
        total_cost = 0.0
        input_tokens = 0
        output_tokens = 0

        async for msg in query(prompt=prompt, options=opts):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
            elif isinstance(msg, ResultMessage):
                total_cost = msg.total_cost_usd or 0.0
                usage = msg.usage or {}
                input_tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
                output_tokens = usage.get("output_tokens", 0) if isinstance(usage, dict) else 0

        return {
            "text": "".join(text_parts),
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": total_cost,
            },
        }
