"""Tests for file-analyzer agent + FileSummary schema + doer_schema_loop."""

from __future__ import annotations

import pytest

from designdoc.agents.file_analyzer import FileSummary, make_file_analyzer
from designdoc.loop import doer_schema_loop
from designdoc.runner import AgentDef, RunResult


class ScriptedRunner:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def run(self, agent, prompt):
        self.calls.append((agent.name, prompt))
        out = self.responses.pop(0)
        return RunResult(text=out, input_tokens=1, output_tokens=1, cost_usd=0.001)


def test_file_summary_requires_purpose():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FileSummary()
    s = FileSummary(purpose="x")
    assert s.purpose == "x"


def test_make_file_analyzer_has_read_tools():
    agent = make_file_analyzer()
    assert "Read" in agent.allowed_tools
    assert agent.system_prompt  # non-empty


@pytest.mark.anyio
async def test_schema_loop_passes_on_valid_first_attempt():
    runner = ScriptedRunner(
        [
            '{"purpose":"does things","key_types":["A"],"key_functions":["f"],"external_deps":[],"notes":""}'
        ]
    )
    doer = AgentDef(name="d", system_prompt="", model="m")
    result = await doer_schema_loop(
        artifact_id="x",
        doer=doer,
        doer_prompt="p",
        schema_model=FileSummary,
        runner=runner,
        hil_sink=[],
    )
    assert result.status == "pass"
    assert result.attempt == 1
    assert len(runner.calls) == 1


@pytest.mark.anyio
async def test_schema_loop_retries_on_invalid_json():
    runner = ScriptedRunner(
        [
            "not json",
            '{"purpose":"fixed"}',
        ]
    )
    doer = AgentDef(name="d", system_prompt="", model="m")
    result = await doer_schema_loop(
        artifact_id="x",
        doer=doer,
        doer_prompt="p",
        schema_model=FileSummary,
        runner=runner,
        hil_sink=[],
    )
    assert result.status == "pass"
    assert result.attempt == 2
    assert len(runner.calls) == 2


@pytest.mark.anyio
async def test_schema_loop_retries_on_missing_required_field():
    """Missing 'purpose' must trigger a retry, with the validation error surfaced."""
    runner = ScriptedRunner(
        [
            '{"key_types":[]}',  # missing purpose
            "still bad",
            '{"purpose":"ok"}',
        ]
    )
    doer = AgentDef(name="d", system_prompt="", model="m")
    result = await doer_schema_loop(
        artifact_id="x",
        doer=doer,
        doer_prompt="p",
        schema_model=FileSummary,
        runner=runner,
        hil_sink=[],
    )
    assert result.status == "pass"
    assert result.attempt == 3
    assert len(runner.calls) == 3

    # Retry prompts must include the validation error text
    retry1_prompt = runner.calls[1][1]
    assert "purpose" in retry1_prompt.lower()


@pytest.mark.anyio
async def test_schema_loop_hil_after_3_fails():
    runner = ScriptedRunner(["bad1", "bad2", "bad3"])
    doer = AgentDef(name="d", system_prompt="", model="m")
    hil_sink: list[dict] = []
    result = await doer_schema_loop(
        artifact_id="x",
        doer=doer,
        doer_prompt="p",
        schema_model=FileSummary,
        runner=runner,
        hil_sink=hil_sink,
        stage_name="file_analysis",
    )
    assert result.status == "shipped_with_hil"
    assert result.attempt == 3
    assert len(runner.calls) == 3
    assert len(hil_sink) == 1
    assert hil_sink[0]["stage"] == "file_analysis"
