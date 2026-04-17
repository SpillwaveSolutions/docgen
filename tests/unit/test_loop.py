"""Tests for doer_checker_loop — the 3-attempt bouncer.

These tests are LOAD-BEARING. If any of them regresses, the system's
reliability claim collapses. Specifically:
- MAX_ATTEMPTS=3 must be enforced in Python, not in a prompt.
- The checker must run in isolation (we use ScriptedRunner to verify call separation).
- On 3 failures, the doc ships with a HIL entry — the pipeline never blocks.
"""
from __future__ import annotations

import pytest

from designdoc.loop import doer_checker_loop
from designdoc.runner import AgentDef, RunResult


class ScriptedRunner:
    """Replays pre-scripted responses keyed by agent name."""

    def __init__(self, by_agent: dict[str, list[str]]):
        self.by_agent = {k: list(v) for k, v in by_agent.items()}
        self.calls: list[tuple[str, str]] = []

    async def run(self, agent, prompt):
        self.calls.append((agent.name, prompt))
        out = self.by_agent[agent.name].pop(0)
        return RunResult(text=out, input_tokens=1, output_tokens=1, cost_usd=0.001)


def _fail_json(attempt: int, fix: str = "f") -> str:
    return (
        f'{{"status":"fail","attempt":{attempt},"artifact_id":"x",'
        f'"issues":[{{"severity":"major","location":"l","current_text":"c","suggested_fix":"{fix}"}}]}}'
    )


@pytest.mark.anyio
async def test_passes_first_attempt():
    runner = ScriptedRunner({
        "doer": ["draft-1"],
        "checker": ['{"status":"pass","summary":"ok"}'],
    })
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    hil_sink: list[dict] = []

    result = await doer_checker_loop(
        artifact_id="x", doer=doer, checker=checker,
        doer_prompt="write x", checker_prompt_fn=lambda d: f"check: {d}",
        runner=runner, hil_sink=hil_sink,
    )

    assert result.status == "pass"
    assert result.attempt == 1
    assert result.text == "draft-1"
    assert hil_sink == []
    # exactly 2 runner calls: doer then checker
    assert len(runner.calls) == 2


@pytest.mark.anyio
async def test_passes_second_attempt_after_fail():
    runner = ScriptedRunner({
        "doer": ["draft-1", "draft-2"],
        "checker": [_fail_json(1), '{"status":"pass","summary":"ok"}'],
    })
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    hil_sink: list[dict] = []

    result = await doer_checker_loop(
        artifact_id="x", doer=doer, checker=checker,
        doer_prompt="p", checker_prompt_fn=lambda d: d,
        runner=runner, hil_sink=hil_sink,
    )

    assert result.status == "pass"
    assert result.attempt == 2
    assert result.text == "draft-2"
    assert hil_sink == []
    # exactly 4 runner calls: doer, checker(fail), doer(retry), checker(pass)
    assert len(runner.calls) == 4


@pytest.mark.anyio
async def test_ships_with_hil_after_3_fails():
    runner = ScriptedRunner({
        "doer": ["d1", "d2", "d3"],
        "checker": [_fail_json(1), _fail_json(2), _fail_json(3)],
    })
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    hil_sink: list[dict] = []

    result = await doer_checker_loop(
        artifact_id="x", doer=doer, checker=checker,
        doer_prompt="p", checker_prompt_fn=lambda d: d,
        runner=runner, hil_sink=hil_sink,
        stage_name="class-docs",
    )

    assert result.status == "shipped_with_hil"
    assert result.attempt == 3
    assert result.text == "d3"
    assert len(hil_sink) == 1
    assert hil_sink[0]["artifact"] == "x"
    assert hil_sink[0]["stage"] == "class-docs"
    assert hil_sink[0]["status"] == "open"
    assert hil_sink[0]["attempts"] == 3
    # exactly 6 runner calls — not 4, not 8. The for loop enforces this.
    assert len(runner.calls) == 6


@pytest.mark.anyio
async def test_malformed_checker_output_counts_as_attempt():
    runner = ScriptedRunner({
        "doer": ["d1", "d2", "d3"],
        "checker": ["not json", "also not json", _fail_json(3)],
    })
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    hil_sink: list[dict] = []

    result = await doer_checker_loop(
        artifact_id="x", doer=doer, checker=checker,
        doer_prompt="p", checker_prompt_fn=lambda d: d,
        runner=runner, hil_sink=hil_sink,
    )

    assert result.status == "shipped_with_hil"
    assert len(hil_sink) == 1
    assert len(runner.calls) == 6


@pytest.mark.anyio
async def test_retry_prompt_contains_only_latest_issues():
    """Retry N must see ONLY the issues from attempt N-1, never accumulated history."""
    runner = ScriptedRunner({
        "doer": ["d1", "d2"],
        "checker": [_fail_json(1, fix="FIX-FROM-ATTEMPT-1"), '{"status":"pass","summary":"ok"}'],
    })
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")

    await doer_checker_loop(
        artifact_id="x", doer=doer, checker=checker,
        doer_prompt="ORIGINAL-TASK", checker_prompt_fn=lambda d: d,
        runner=runner, hil_sink=[],
    )

    # Third call is the doer retry — inspect its prompt
    retry_call = runner.calls[2]
    agent_name, retry_prompt = retry_call
    assert agent_name == "doer"
    assert "FIX-FROM-ATTEMPT-1" in retry_prompt  # latest issue
    assert "ORIGINAL-TASK" in retry_prompt       # original task included
