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
    fake = FakeSDK([
        {"text": "hello world",
         "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01}},
    ])
    r = ClaudeSDKRunner(budget=budget, sdk=fake)
    agent = AgentDef(name="t", system_prompt="p", model="claude-sonnet-4-6",
                     allowed_tools=[], max_output_tokens=1024)

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
    fake = FakeSDK([{"text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}])
    r = ClaudeSDKRunner(budget=budget, sdk=fake)
    agent = AgentDef(
        name="checker", system_prompt="you are a checker", model="claude-haiku-4-5-20251001",
        allowed_tools=["Read", "Grep"], max_output_tokens=2048,
    )

    await r.run(agent, prompt="check this")

    assert len(fake.calls) == 1
    prompt, options = fake.calls[0]
    assert prompt == "check this"
    assert options["system_prompt"] == "you are a checker"
    assert options["model"] == "claude-haiku-4-5-20251001"
    assert options["allowed_tools"] == ["Read", "Grep"]


@pytest.mark.anyio
async def test_runner_budget_exceeded_propagates():
    budget = CostAccumulator(cap_usd=0.10)
    fake = FakeSDK([
        {"text": "a", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.11}},
    ])
    r = ClaudeSDKRunner(budget=budget, sdk=fake)
    agent = AgentDef(name="t", system_prompt="", model="m")

    with pytest.raises(BudgetExceededError):
        await r.run(agent, prompt="hi")

    # budget must be intact — the failed invocation did NOT mutate state
    assert budget.total_cost_usd == 0.0
    assert budget.invocations == 0
