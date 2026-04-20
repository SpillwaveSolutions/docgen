"""Stage 5 within-stage resume: per-class diagram checkpoint.

Tests that artifact_index["mermaid:<rel>"] is written after a successful
diagram generation, and that a second run with the same class-doc body makes
zero LLM calls (the checkpoint fires).

These tests use a FakeSDK (no real mmdc required for the checkpoint-only
assertions) but are gated by requires_mmdc because the full mermaid loop
always runs mmdc for validation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages import s5_mermaid
from designdoc.state import PipelineState


class _CountingFakeSDK:
    """Counts LLM calls and returns valid mermaid + pass verdict."""

    def __init__(self) -> None:
        self.call_count: int = 0

    async def query(self, *, prompt: str, options: dict) -> dict:
        self.call_count += 1
        system = options.get("system_prompt") or ""
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
        raise AssertionError(f"unexpected system prompt: {system[:80]}")


def _make_runner(sdk: _CountingFakeSDK) -> ClaudeSDKRunner:
    return ClaudeSDKRunner(budget=CostAccumulator(cap_usd=5.0), sdk=sdk)


def _seed_class_doc(output: Path, pkg: str, class_name: str, body: str) -> Path:
    classes_dir = output / "packages" / pkg / "classes"
    classes_dir.mkdir(parents=True, exist_ok=True)
    doc = classes_dir / f"{class_name}.md"
    doc.write_text(body)
    return doc


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_stage5_checkpoints_per_class_diagram(tmp_path: Path) -> None:
    """After a successful run, artifact_index has a 'mermaid:<rel>' entry
    with a non-empty input_hash."""
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_class_doc(output, "alpha", "Foo", "# Foo\n\nclass doc\n")

    sdk = _CountingFakeSDK()
    await s5_mermaid.run(state=state, runner=_make_runner(sdk), skip_preflight=False)

    rel = "packages/alpha/classes/Foo.md"
    assert f"mermaid:{rel}" in state.artifact_index, (
        "artifact_index must have an entry for the generated mermaid diagram"
    )
    entry = state.artifact_index[f"mermaid:{rel}"]
    assert entry["input_hash"] != "", "input_hash must be non-empty"
    assert entry["path"] == rel, "path must match the class doc relative path"


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_stage5_rerun_skips_checkpointed_diagrams(tmp_path: Path) -> None:
    """Two-run test: first run produces mermaid diagram + artifact_index entry;
    second run with same class-doc body must make ZERO LLM calls."""
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_class_doc(output, "payments", "Gateway", "# Gateway\n\npayment class\n")

    # First run: LLM is called.
    sdk1 = _CountingFakeSDK()
    await s5_mermaid.run(state=state, runner=_make_runner(sdk1), skip_preflight=False)
    assert sdk1.call_count > 0, "first run must make LLM calls"

    # Verify artifact_index entry was written.
    rel = "packages/payments/classes/Gateway.md"
    assert f"mermaid:{rel}" in state.artifact_index
    first_hash = state.artifact_index[f"mermaid:{rel}"]["input_hash"]
    assert first_hash != ""

    # Reload state from disk (simulating a crash-resume).
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)

    # Second run: class doc body unchanged -> zero LLM calls.
    sdk2 = _CountingFakeSDK()
    await s5_mermaid.run(state=state2, runner=_make_runner(sdk2), skip_preflight=False)
    assert sdk2.call_count == 0, (
        f"unchanged class doc made {sdk2.call_count} LLM calls on second run; expected 0"
    )


@pytest.mark.requires_mmdc
@pytest.mark.anyio
async def test_stage5_partial_crash_resume(tmp_path: Path) -> None:
    """Simulate a mid-stage crash: pre-populate artifact_index for one class doc,
    verify that only the other class doc is processed on the next run."""
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)

    foo_doc = _seed_class_doc(output, "alpha", "Foo", "# Foo\n\nfoo class\n")
    _seed_class_doc(output, "alpha", "Bar", "# Bar\n\nbar class\n")

    # Simulate Foo having been completed before crash: write its Diagram section
    # and populate artifact_index + rollup_hashes.
    import hashlib

    foo_body = "# Foo\n\nfoo class\n"
    foo_hash = hashlib.sha1(foo_body.encode("utf-8")).hexdigest()
    foo_doc.write_text(
        foo_body.rstrip() + "\n\n## Diagram\n\n```mermaid\nflowchart TD\n    A --> B\n```\n"
    )

    rel_foo = "packages/alpha/classes/Foo.md"
    state.artifact_index[f"mermaid:{rel_foo}"] = {
        "path": rel_foo,
        "input_hash": foo_hash,
    }
    state.rollup_hashes[f"mermaid:{rel_foo}"] = foo_hash
    state.save()

    # Now run: only Bar should be processed (Foo is checkpointed).
    sdk = _CountingFakeSDK()
    diagrams = await s5_mermaid.run(state=state, runner=_make_runner(sdk), skip_preflight=False)

    assert sdk.call_count > 0, "Bar must be processed (LLM calls expected)"
    # 2 calls: doer + checker for Bar only
    assert sdk.call_count == 2, f"expected 2 LLM calls for Bar only, got {sdk.call_count}"
    rel_bar = "packages/alpha/classes/Bar.md"
    assert f"mermaid:{rel_bar}" in state.artifact_index, "Bar must be checkpointed after processing"
    # Foo should appear in diagrams (returned from skip path).
    assert rel_foo in diagrams
    assert rel_bar in diagrams
