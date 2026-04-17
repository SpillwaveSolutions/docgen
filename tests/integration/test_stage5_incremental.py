"""Stage 5 incremental: skip mermaid regeneration when the class-doc body
(excluding its existing Diagram section) hasn't changed since the last run.

Skip key: "mermaid:<doc-relative-path>" in state.rollup_hashes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages.s5_mermaid import run as stage_mermaid
from designdoc.state import PipelineState


class CountingFakeSDK:
    def __init__(self):
        self.calls: list[str] = []

    async def query(self, *, prompt, options):
        system = options.get("system_prompt") or ""
        self.calls.append(system[:40])
        if "mermaid diagram generator" in system:
            return {
                "text": "flowchart TD\n    A --> B\n",
                "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
            }
        if "mermaid-semantics reviewer" in system:
            return {
                "text": json.dumps({"status": "pass", "summary": "ok"}),
                "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0005},
            }
        raise AssertionError(f"unexpected agent: {system[:80]}")


def _seed_class_docs(output: Path, docs: dict[str, str]) -> None:
    for rel, body in docs.items():
        p = output / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


async def _run_stage5(state, fake):
    runner = ClaudeSDKRunner(budget=CostAccumulator(cap_usd=5.0), sdk=fake)
    return await stage_mermaid(state=state, runner=runner)


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_second_run_with_unchanged_class_docs_skips_mermaid(tmp_path: Path):
    output = tmp_path / "design"
    _seed_class_docs(
        output,
        {
            "packages/payments/classes/Gateway.md": "## Purpose\nGateway.\n",
            "packages/payments/classes/Charge.md": "## Purpose\nCharge.\n",
        },
    )
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    fake1 = CountingFakeSDK()
    await _run_stage5(state, fake1)
    first_calls = len(fake1.calls)
    assert first_calls > 0

    # Second run: class docs have Diagram sections now but their PRE-Diagram
    # bodies haven't changed — Stage 5 must skip entirely.
    fake2 = CountingFakeSDK()
    await _run_stage5(state, fake2)
    assert fake2.calls == [], (
        f"unchanged class docs made {len(fake2.calls)} mermaid calls; should be 0"
    )


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_second_run_does_not_duplicate_diagram_section(tmp_path: Path):
    """Pre-existing bug: Stage 5 used to append a new Diagram section on every
    run. With incremental skip, the doc is left untouched."""
    output = tmp_path / "design"
    doc_path = output / "packages" / "payments" / "classes" / "Gateway.md"
    _seed_class_docs(output, {str(doc_path.relative_to(output)): "## Purpose\nGateway.\n"})
    state = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    await _run_stage5(state, CountingFakeSDK())
    first_content = doc_path.read_text()
    assert first_content.count("## Diagram") == 1

    await _run_stage5(state, CountingFakeSDK())
    assert doc_path.read_text() == first_content, (
        "second run must not modify an unchanged class doc"
    )
    assert doc_path.read_text().count("## Diagram") == 1
