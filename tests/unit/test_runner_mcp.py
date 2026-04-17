"""Tests for MCP server passthrough in ClaudeSDKRunner.

AgentDef.mcp_servers carries short names ("perplexity", "context7"). The
runner must translate those into what ClaudeAgentOptions expects AND expose
the corresponding tool-name patterns in allowed_tools so the agent can
actually invoke them.
"""

from __future__ import annotations

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import AgentDef, ClaudeSDKRunner


class CapturingSDK:
    """Records the options passed to query() so tests can assert on them."""

    def __init__(self):
        self.last_options: dict | None = None

    async def query(self, *, prompt, options):
        self.last_options = options
        return {"text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}


@pytest.mark.anyio
async def test_no_mcp_servers_passes_empty_config():
    """Agents without MCP should not have MCP-related options set."""
    budget = CostAccumulator(cap_usd=1.0)
    sdk = CapturingSDK()
    r = ClaudeSDKRunner(budget=budget, sdk=sdk)

    agent = AgentDef(name="t", system_prompt="p", model="claude-sonnet-4-6", mcp_servers=[])
    await r.run(agent, prompt="hi")

    assert sdk.last_options is not None
    # Empty list is fine — the runner shouldn't invent servers
    assert sdk.last_options.get("mcp_servers") in ([], None)


@pytest.mark.anyio
async def test_mcp_servers_flow_to_options():
    budget = CostAccumulator(cap_usd=1.0)
    sdk = CapturingSDK()
    r = ClaudeSDKRunner(budget=budget, sdk=sdk)

    agent = AgentDef(
        name="researcher",
        system_prompt="p",
        model="claude-sonnet-4-6",
        mcp_servers=["perplexity", "context7"],
    )
    await r.run(agent, prompt="hi")

    opts = sdk.last_options
    assert opts is not None
    assert opts.get("mcp_servers") == ["perplexity", "context7"]


@pytest.mark.anyio
async def test_mcp_servers_expand_allowed_tools_with_tool_patterns():
    """When MCP servers are declared, the corresponding mcp__<server>__* tool
    patterns must be in allowed_tools so agents can actually call them."""
    budget = CostAccumulator(cap_usd=1.0)
    sdk = CapturingSDK()
    r = ClaudeSDKRunner(budget=budget, sdk=sdk)

    agent = AgentDef(
        name="researcher",
        system_prompt="p",
        model="claude-sonnet-4-6",
        allowed_tools=["Read"],
        mcp_servers=["perplexity"],
    )
    await r.run(agent, prompt="hi")

    tools = sdk.last_options["allowed_tools"]
    assert "Read" in tools  # original tools preserved
    assert any(t.startswith("mcp__perplexity__") or t == "mcp__perplexity" for t in tools), (
        f"expected mcp__perplexity__ pattern in allowed_tools, got {tools}"
    )


@pytest.mark.anyio
async def test_mcp_servers_set_setting_sources_for_config_inheritance():
    """Setting sources must include 'user' and 'project' so MCP servers
    configured in ~/.claude.json or .mcp.json flow through."""
    budget = CostAccumulator(cap_usd=1.0)
    sdk = CapturingSDK()
    r = ClaudeSDKRunner(budget=budget, sdk=sdk)

    agent = AgentDef(
        name="researcher",
        system_prompt="p",
        model="claude-sonnet-4-6",
        mcp_servers=["perplexity"],
    )
    await r.run(agent, prompt="hi")

    sources = sdk.last_options.get("setting_sources") or []
    assert "user" in sources
    assert "project" in sources
