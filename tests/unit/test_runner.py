"""Tests for ClaudeSDKRunner with a fake SDK.

Runner must:
- accrue cost to the CostAccumulator on every invocation
- return a RunResult with text + token counts + cost
- raise BudgetExceededError (via the accumulator) when projected cap exceeded

The real claude_agent_sdk is NOT exercised here — we inject a fake to keep unit
tests fast and offline. The live SDK path is exercised in tests/e2e.
"""

from __future__ import annotations

import pytest

from designdoc.budget import BudgetExceededError, CostAccumulator
from designdoc.runner import AgentDef, ClaudeSDKRunner


class FakeSDK:
    """In-memory fake that replays pre-scripted responses."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def query(self, *, prompt: str, options: dict) -> dict:
        self.calls.append((prompt, options))
        return self.responses.pop(0)


@pytest.mark.anyio
async def test_runner_records_cost_and_returns_text():
    budget = CostAccumulator(cap_usd=1.00)
    fake = FakeSDK(
        [
            {
                "text": "hello world",
                "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01},
            },
        ]
    )
    r = ClaudeSDKRunner(budget=budget, sdk=fake)
    agent = AgentDef(
        name="t",
        system_prompt="p",
        model="claude-sonnet-4-6",
        allowed_tools=[],
        max_output_tokens=1024,
    )

    out = await r.run(agent, prompt="hi")

    assert out.text == "hello world"
    assert out.input_tokens == 100
    assert out.output_tokens == 50
    assert out.cost_usd == pytest.approx(0.01)
    assert budget.total_cost_usd == pytest.approx(0.01)
    assert budget.invocations == 1
    assert budget.by_agent == {"t": pytest.approx(0.01)}


@pytest.mark.anyio
async def test_runner_passes_agent_config_to_sdk():
    budget = CostAccumulator(cap_usd=1.00)
    fake = FakeSDK(
        [{"text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}]
    )
    r = ClaudeSDKRunner(budget=budget, sdk=fake)
    agent = AgentDef(
        name="checker",
        system_prompt="you are a checker",
        model="claude-haiku-4-5-20251001",
        allowed_tools=["Read", "Grep"],
        max_output_tokens=2048,
    )

    await r.run(agent, prompt="check this")

    assert len(fake.calls) == 1
    prompt, options = fake.calls[0]
    assert prompt == "check this"
    assert options["system_prompt"] == "you are a checker"
    assert options["model"] == "claude-haiku-4-5-20251001"
    assert options["allowed_tools"] == ["Read", "Grep"]


@pytest.mark.anyio
async def test_runner_passes_cwd_to_sdk_options():
    """Issue #46: when the runner is configured with a cwd, every options
    dict passed to the SDK must contain it. This is what lets the SDK
    subprocess Read source files in the target repo when the user invokes
    designdoc from a different working directory.
    """
    budget = CostAccumulator(cap_usd=1.00)
    fake = FakeSDK(
        [{"text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}]
    )
    r = ClaudeSDKRunner(budget=budget, sdk=fake, cwd="/tmp/some-target-repo")
    agent = AgentDef(name="t", system_prompt="", model="m")

    await r.run(agent, prompt="hi")

    assert len(fake.calls) == 1
    _, options = fake.calls[0]
    assert options["cwd"] == "/tmp/some-target-repo"


@pytest.mark.anyio
async def test_runner_omits_cwd_when_none():
    """Backward-compat: if no cwd is supplied, the options dict must not
    include a cwd key (the SDK falls back to its default behavior)."""
    budget = CostAccumulator(cap_usd=1.00)
    fake = FakeSDK(
        [{"text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}]
    )
    r = ClaudeSDKRunner(budget=budget, sdk=fake)  # no cwd
    agent = AgentDef(name="t", system_prompt="", model="m")

    await r.run(agent, prompt="hi")

    _, options = fake.calls[0]
    assert "cwd" not in options


@pytest.mark.anyio
async def test_runner_uses_out_of_tree_cwd(tmp_path):
    """Issue #46 regression: pytest's tmp_path is outside the docgen tree
    by design. This is the closest CI-level approximation of the
    realistic case 'user runs designdoc from /Users/me/work, target repo
    is /Users/me/projects/foo' that surfaced the bug in the agent-brain
    eval. The captured cwd must match the target path string-for-string.
    """
    target = tmp_path / "target_repo"
    target.mkdir()
    (target / "main.py").write_text("class X:\n    pass\n")

    budget = CostAccumulator(cap_usd=1.00)
    fake = FakeSDK(
        [{"text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}]
    )
    r = ClaudeSDKRunner(budget=budget, sdk=fake, cwd=str(target))
    agent = AgentDef(name="class-documenter", system_prompt="", model="m", allowed_tools=["Read"])

    await r.run(agent, prompt="document the class in main.py")

    _, options = fake.calls[0]
    assert options["cwd"] == str(target)
    assert "Read" in options["allowed_tools"], (
        "Read must be enabled — issue #46 is about Read access in the target repo"
    )


@pytest.mark.anyio
async def test_runner_isolates_non_mcp_agents():
    """Issue #49: doers/checkers without MCP servers should run hermetic —
    the SDK must NOT load user/project CLAUDE.md, output styles, or other
    ambient config that would pollute their output (e.g. ★ Insight ─── blocks
    inherited from the user's session output-style preference).

    The SDK contract: setting_sources=[] means "isolation mode, no filesystem
    settings." That's what we want for class_documenter and friends.
    """
    budget = CostAccumulator(cap_usd=1.00)
    fake = FakeSDK(
        [{"text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}]
    )
    r = ClaudeSDKRunner(budget=budget, sdk=fake)
    agent = AgentDef(
        name="class-documenter",
        system_prompt="hermetic prompt",
        model="m",
        allowed_tools=["Read", "Grep"],
        # NOTE: no mcp_servers
    )

    await r.run(agent, prompt="document a class")

    _, options = fake.calls[0]
    assert "setting_sources" in options, (
        "non-MCP agents must explicitly request isolation mode (setting_sources=[]) "
        "rather than rely on SDK default behavior, which loads ALL settings"
    )
    assert options["setting_sources"] == [], (
        "non-MCP agents must run hermetic — empty setting_sources list"
    )


@pytest.mark.anyio
async def test_runner_loads_settings_for_mcp_agents():
    """MCP-using agents (e.g. tech_debt researcher with Perplexity / Context7)
    NEED user/project settings to discover MCP server configs declared in
    ~/.claude.json or .mcp.json. So setting_sources stays as user+project+local
    in that case — only the non-MCP path becomes hermetic."""
    budget = CostAccumulator(cap_usd=1.00)
    fake = FakeSDK(
        [{"text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}]
    )
    r = ClaudeSDKRunner(budget=budget, sdk=fake)
    agent = AgentDef(
        name="tech-debt-researcher",
        system_prompt="research deps",
        model="m",
        allowed_tools=["Read"],
        mcp_servers=["perplexity-ask", "context7"],
    )

    await r.run(agent, prompt="research")

    _, options = fake.calls[0]
    assert options["setting_sources"] == ["user", "project", "local"]


@pytest.mark.anyio
async def test_runner_budget_exceeded_propagates():
    budget = CostAccumulator(cap_usd=0.10)
    fake = FakeSDK(
        [
            {"text": "a", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.11}},
        ]
    )
    r = ClaudeSDKRunner(budget=budget, sdk=fake)
    agent = AgentDef(name="t", system_prompt="", model="m")

    with pytest.raises(BudgetExceededError):
        await r.run(agent, prompt="hi")

    # budget must be intact — the failed invocation did NOT mutate state
    assert budget.total_cost_usd == 0.0
    assert budget.invocations == 0
