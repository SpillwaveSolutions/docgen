"""Unit tests for the mermaid two-checker composite.

strip_fence is pure — test it without subprocess calls.
The composite loop is tested with a FakeRunner that intercepts checker calls.
"""

from __future__ import annotations

import json

import pytest

from designdoc.mermaid.loop import strip_fence


def test_strip_fence_removes_mermaid_fence():
    src = "```mermaid\nflowchart TD\n    A --> B\n```"
    assert strip_fence(src) == "flowchart TD\n    A --> B"


def test_strip_fence_removes_plain_fence():
    src = "```\nflowchart TD\n    A --> B\n```"
    assert strip_fence(src) == "flowchart TD\n    A --> B"


def test_strip_fence_noop_on_already_plain():
    src = "flowchart TD\n    A --> B"
    assert strip_fence(src) == "flowchart TD\n    A --> B"


def test_strip_fence_handles_whitespace():
    src = "\n\n```mermaid\nflowchart LR\n  A --> B\n```\n\n"
    assert strip_fence(src).startswith("flowchart LR")


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_mermaid_loop_passes_on_valid_diagram(tmp_path):
    """End-to-end: generator returns valid syntax, semantic checker says pass."""
    from designdoc.budget import CostAccumulator
    from designdoc.mermaid.loop import generate_validated_mermaid
    from designdoc.runner import ClaudeSDKRunner

    class FakeSDK:
        async def query(self, *, prompt: str, options: dict) -> dict:
            system = options.get("system_prompt") or ""
            if "mermaid diagram generator" in system:
                return {
                    "text": "flowchart TD\n    A --> B\n    B --> C\n",
                    "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
                }
            if "mermaid-semantics reviewer" in system:
                return {
                    "text": json.dumps({"status": "pass", "summary": "ok"}),
                    "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0005},
                }
            raise AssertionError(f"unexpected agent: {system[:60]}")

    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())
    result = await generate_validated_mermaid(
        artifact_name="Foo",
        artifact_text="## Foo\nDepends on A, B, C.",
        runner=runner,
        hil_sink=[],
    )
    assert result.status == "pass"
    assert result.attempt == 1


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_mermaid_loop_hil_on_persistent_syntax_failure():
    """Generator returns bad syntax 3x — must ship with HIL."""
    from designdoc.budget import CostAccumulator
    from designdoc.mermaid.loop import generate_validated_mermaid
    from designdoc.runner import ClaudeSDKRunner

    class FakeSDK:
        async def query(self, *, prompt: str, options: dict) -> dict:
            system = options.get("system_prompt") or ""
            if "mermaid diagram generator" in system:
                # Deliberate bad syntax
                return {
                    "text": "flowchart TD\n    A -->> B ^^ nonsense\n",
                    "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
                }
            raise AssertionError(f"syntax should fail first: {system[:60]}")

    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=1.0), sdk=FakeSDK())
    hil_sink: list[dict] = []
    result = await generate_validated_mermaid(
        artifact_name="Foo",
        artifact_text="## Foo",
        runner=runner,
        hil_sink=hil_sink,
    )
    assert result.status == "shipped_with_hil"
    assert len(hil_sink) == 1
