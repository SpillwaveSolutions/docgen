"""Tests for doer_checker_loop — the 3-attempt bouncer.

These tests are LOAD-BEARING. If any of them regresses, the system's
reliability claim collapses. Specifically:
- MAX_ATTEMPTS=3 must be enforced in Python, not in a prompt.
- The checker must run in isolation (we use ScriptedRunner to verify call separation).
- On 3 failures, the doc ships with a HIL entry — the pipeline never blocks.
"""

from __future__ import annotations

import json

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
