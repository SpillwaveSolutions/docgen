"""Tests for doer_checker_loop — the 3-attempt bouncer.

These tests are LOAD-BEARING. If any of them regresses, the system's
reliability claim collapses. Specifically:
- MAX_ATTEMPTS=3 must be enforced in Python, not in a prompt.
- The checker must run in isolation (we use ScriptedRunner to verify call separation).
- On 3 failures, the doc ships with a HIL entry — the pipeline never blocks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.loop import doer_checker_loop
from designdoc.runner import AgentDef, RunResult
from designdoc.state import PipelineState


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
    runner = ScriptedRunner(
        {
            "doer": ["draft-1"],
            "checker": ['{"status":"pass","summary":"ok"}'],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    hil_sink: list[dict] = []

    result = await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="write x",
        checker_prompt_fn=lambda d: f"check: {d}",
        runner=runner,
        hil_sink=hil_sink,
    )

    assert result.status == "pass"
    assert result.attempt == 1
    assert result.text == "draft-1"
    assert hil_sink == []
    # exactly 2 runner calls: doer then checker
    assert len(runner.calls) == 2


@pytest.mark.anyio
async def test_passes_second_attempt_after_fail():
    runner = ScriptedRunner(
        {
            "doer": ["draft-1", "draft-2"],
            "checker": [_fail_json(1), '{"status":"pass","summary":"ok"}'],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    hil_sink: list[dict] = []

    result = await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=hil_sink,
    )

    assert result.status == "pass"
    assert result.attempt == 2
    assert result.text == "draft-2"
    assert hil_sink == []
    # exactly 4 runner calls: doer, checker(fail), doer(retry), checker(pass)
    assert len(runner.calls) == 4


@pytest.mark.anyio
async def test_ships_with_hil_after_3_fails():
    runner = ScriptedRunner(
        {
            "doer": ["d1", "d2", "d3"],
            "checker": [_fail_json(1), _fail_json(2), _fail_json(3)],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    hil_sink: list[dict] = []

    result = await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=hil_sink,
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
    runner = ScriptedRunner(
        {
            "doer": ["d1", "d2", "d3"],
            "checker": ["not json", "also not json", _fail_json(3)],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    hil_sink: list[dict] = []

    result = await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=hil_sink,
    )

    assert result.status == "shipped_with_hil"
    assert len(hil_sink) == 1
    assert len(runner.calls) == 6


@pytest.mark.anyio
async def test_debug_capture_written_on_parse_failure(tmp_path):
    """When debug_dir is set, raw checker output is persisted for every attempt,
    including synthetic-fail attempts. Proves INV-001 diagnostic works."""
    runner = ScriptedRunner(
        {
            "doer": ["d1", "d2", "d3"],
            "checker": ["not valid json", "```json\n{broken}\n```", "still broken"],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    debug_dir = tmp_path / "captures"

    await doer_checker_loop(
        artifact_id="pkg/mod.py::MyClass",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=[],
        stage_name="class_docs",
        debug_dir=debug_dir,
    )

    files = sorted(debug_dir.glob("*.json"))
    assert len(files) == 3, f"expected 3 captures, got {[f.name for f in files]}"

    first = json.loads(files[0].read_text())
    assert first["artifact_id"] == "pkg/mod.py::MyClass"
    assert first["stage"] == "class_docs"
    assert first["attempt"] == 1
    assert first["raw_output"] == "not valid json"
    assert first["raw_output_length"] == len("not valid json")
    assert first["parse_status"] == "fail"
    assert first["parse_exception"] == "JSONDecodeError"

    # Filename must be filesystem-safe — no "/" or "::" verbatim
    for f in files:
        assert "/" not in f.name
        assert "::" not in f.name

    # Second capture preserved its code-fence wrapper verbatim — the diagnostic's
    # whole purpose is to show *what* the checker emitted, not a cleaned version.
    second = json.loads(files[1].read_text())
    assert second["raw_output"] == "```json\n{broken}\n```"


@pytest.mark.anyio
async def test_debug_capture_written_on_pass(tmp_path):
    """Success captures are also written — lets us compare passing vs failing output format."""
    runner = ScriptedRunner(
        {
            "doer": ["d1"],
            "checker": ['{"status":"pass","summary":"ok"}'],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    debug_dir = tmp_path / "captures"

    await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=[],
        debug_dir=debug_dir,
    )

    files = list(debug_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["parse_status"] == "pass"
    assert data["parse_exception"] is None


@pytest.mark.anyio
async def test_no_debug_capture_when_debug_dir_none(tmp_path, monkeypatch):
    """Default behavior — nothing written to disk when debug_dir is None
    AND env var is unset. The opt-in contract must stay opt-in."""
    monkeypatch.delenv("DESIGNDOC_DEBUG_DIR", raising=False)
    runner = ScriptedRunner(
        {
            "doer": ["d1"],
            "checker": ['{"status":"pass","summary":"ok"}'],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")

    await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=[],
    )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_debug_capture_honors_env_var(tmp_path, monkeypatch):
    """DESIGNDOC_DEBUG_DIR env var enables capture without any call-site changes.
    This is how real pipeline stages activate the diagnostic."""
    env_dir = tmp_path / "env_captures"
    monkeypatch.setenv("DESIGNDOC_DEBUG_DIR", str(env_dir))
    runner = ScriptedRunner(
        {
            "doer": ["d1", "d2", "d3"],
            "checker": ["not json", "also not json", "still not json"],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")

    # No debug_dir passed — env var should take over
    await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=[],
        stage_name="test",
    )

    files = sorted(env_dir.glob("*.json"))
    assert len(files) == 3, "env var should trigger capture on every attempt"
    data = json.loads(files[0].read_text())
    assert data["parse_exception"] == "JSONDecodeError"


@pytest.mark.anyio
async def test_explicit_debug_dir_overrides_env_var(tmp_path, monkeypatch):
    """Explicit debug_dir parameter wins over env var. Preserves testability
    — a test passing tmp_path shouldn't be affected by ambient env."""
    env_dir = tmp_path / "env_should_not_receive"
    explicit_dir = tmp_path / "explicit"
    monkeypatch.setenv("DESIGNDOC_DEBUG_DIR", str(env_dir))
    runner = ScriptedRunner(
        {
            "doer": ["d1"],
            "checker": ['{"status":"pass","summary":"ok"}'],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")

    await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=[],
        debug_dir=explicit_dir,
    )

    assert list(explicit_dir.glob("*.json")), "explicit dir received the capture"
    assert not env_dir.exists(), "env dir was untouched"


@pytest.mark.anyio
async def test_retry_prompt_contains_only_latest_issues():
    """Retry N must see ONLY the issues from attempt N-1, never accumulated history."""
    runner = ScriptedRunner(
        {
            "doer": ["d1", "d2"],
            "checker": [
                _fail_json(1, fix="FIX-FROM-ATTEMPT-1"),
                '{"status":"pass","summary":"ok"}',
            ],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")

    await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="ORIGINAL-TASK",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=[],
    )

    # Third call is the doer retry — inspect its prompt
    retry_call = runner.calls[2]
    agent_name, retry_prompt = retry_call
    assert agent_name == "doer"
    assert "FIX-FROM-ATTEMPT-1" in retry_prompt  # latest issue
    assert "ORIGINAL-TASK" in retry_prompt  # original task included


def _make_state(tmp_path: Path) -> PipelineState:
    return PipelineState(target_repo=Path("/x"), output_dir=tmp_path)


@pytest.mark.anyio
async def test_parse_failure_retries_bump_checker_parse_counter(tmp_path):
    """Three parse-failures → ships with HIL after 2 retries. Both retries
    are checker-parse retries (synthetic-fail verdict). The terminal attempt
    is NOT counted — it routes through _ship_with_hil, not the retry branch."""
    runner = ScriptedRunner(
        {
            "doer": ["d1", "d2", "d3"],
            "checker": ["not json", "also not json", "still not json"],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    state = _make_state(tmp_path)

    result = await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=state.hil_issues,
        state=state,
    )

    assert result.status == "shipped_with_hil"
    assert state.checker_parse_retries == 2
    assert state.doer_content_retries == 0


@pytest.mark.anyio
async def test_content_failure_retries_bump_doer_content_counter(tmp_path):
    """Three real-fail verdicts → ships with HIL after 2 retries. Both retries
    are doer-content retries (genuine checker objections). Terminal not counted."""
    runner = ScriptedRunner(
        {
            "doer": ["d1", "d2", "d3"],
            "checker": [_fail_json(1), _fail_json(2), _fail_json(3)],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    state = _make_state(tmp_path)

    result = await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=state.hil_issues,
        state=state,
    )

    assert result.status == "shipped_with_hil"
    assert state.doer_content_retries == 2
    assert state.checker_parse_retries == 0


@pytest.mark.anyio
async def test_mixed_failures_bump_both_counters_then_pass(tmp_path):
    """Parse-fail → real-fail → pass. Two retries total, one of each kind.
    Final pass does NOT increment either counter — the counters record
    retries, not attempts."""
    runner = ScriptedRunner(
        {
            "doer": ["d1", "d2", "d3"],
            "checker": [
                "not json",  # synthetic-fail → checker_parse_retries
                _fail_json(2),  # real fail → doer_content_retries
                '{"status":"pass","summary":"ok"}',  # pass
            ],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    state = _make_state(tmp_path)

    result = await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=state.hil_issues,
        state=state,
    )

    assert result.status == "pass"
    assert state.checker_parse_retries == 1
    assert state.doer_content_retries == 1


@pytest.mark.anyio
async def test_state_param_optional_preserves_backward_compat(tmp_path):
    """Omitting state= must not crash — it's a kw-only default-None param so
    in-tree tests that predate the split still work unchanged."""
    runner = ScriptedRunner(
        {
            "doer": ["d1", "d2"],
            "checker": ["not json", '{"status":"pass","summary":"ok"}'],
        }
    )
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")

    result = await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        doer_prompt="p",
        checker_prompt_fn=lambda d: d,
        runner=runner,
        hil_sink=[],
    )
    assert result.status == "pass"


# Invariant 2 guard (no self-grading): the checker must NEVER see content from
# the doer's prior attempts beyond the current draft text. The retry prompt may
# include "previous output (for reference)" for the DOER, but the CHECKER's
# prompt always derives only from `current_text` (the doer's latest output).
# This test locks that contract in at the type level.


@pytest.mark.anyio
async def test_invariant_2_checker_never_sees_prior_attempts() -> None:
    """The checker prompt must derive ONLY from the current draft, never from
    prior-attempt content or the doer's scratchpad. Regression guard for
    Invariant 2 (no self-grading) per CLAUDE.md.
    """
    # Three attempts: doer produces draft-1 then draft-2 then draft-3.
    # Checker rejects the first two with distinct issue text we can detect.
    runner = ScriptedRunner(
        {
            "doer": ["DRAFT-ONE", "DRAFT-TWO", "DRAFT-THREE"],
            "checker": [
                _fail_json(1, fix="objection-A"),
                _fail_json(2, fix="objection-B"),
                '{"status":"pass","summary":"ok"}',
            ],
        }
    )
    doer = AgentDef(name="doer", system_prompt="DOER-SYS-PROMPT", model="m")
    checker = AgentDef(name="checker", system_prompt="CHECKER-SYS-PROMPT", model="m")

    await doer_checker_loop(
        artifact_id="x",
        doer=doer,
        checker=checker,
        # Pass the draft straight through — the simplest possible checker_prompt_fn.
        # If a future refactor were to start including doer prompts/scratchpad here,
        # the assertions below would catch it.
        checker_prompt_fn=lambda d: d,
        doer_prompt="ORIGINAL-TASK",
        runner=runner,
        hil_sink=[],
    )

    # Filter for checker-only invocations.
    checker_calls = [(name, prompt) for (name, prompt) in runner.calls if name == "checker"]
    assert len(checker_calls) == 3, "expected three checker invocations across the loop"

    for attempt_idx, (_, prompt) in enumerate(checker_calls, start=1):
        # Doer's system prompt must never leak into the checker.
        assert "DOER-SYS-PROMPT" not in prompt, (
            f"checker attempt {attempt_idx} saw doer system prompt — invariant 2 violated"
        )
        # Doer's original task prompt must never leak into the checker.
        assert "ORIGINAL-TASK" not in prompt, (
            f"checker attempt {attempt_idx} saw original doer prompt — invariant 2 violated"
        )
        # Prior checker objections must never leak into a later checker call.
        if attempt_idx >= 2:
            assert "objection-A" not in prompt, (
                f"checker attempt {attempt_idx} saw attempt-1 objection — invariant 2 violated"
            )
        if attempt_idx >= 3:
            assert "objection-B" not in prompt, (
                f"checker attempt {attempt_idx} saw attempt-2 objection — invariant 2 violated"
            )

    # Each checker call gets exactly the doer's draft for that attempt — nothing else.
    assert checker_calls[0][1] == "DRAFT-ONE"
    assert checker_calls[1][1] == "DRAFT-TWO"
    assert checker_calls[2][1] == "DRAFT-THREE"
