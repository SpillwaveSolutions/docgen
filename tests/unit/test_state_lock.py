"""Concurrent save safety and atomic state.json writes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from designdoc.state import PipelineState, state_lock


@pytest.mark.anyio("asyncio")
async def test_concurrent_saves_under_lock_preserve_last_write(tmp_path: Path) -> None:
    """50 concurrent mutators + saves; final state contains all 50 entries."""
    output = tmp_path / "out"
    output.mkdir()
    s = PipelineState(target_repo=tmp_path, output_dir=output)

    async def mutator(i: int) -> None:
        async with state_lock:
            s.artifact_index[f"id-{i}"] = {"path": f"p{i}", "input_hash": f"h{i}"}
            s.save()

    await asyncio.gather(*[mutator(i) for i in range(50)])

    loaded = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    assert len(loaded.artifact_index) == 50
    for i in range(50):
        assert loaded.artifact_index[f"id-{i}"] == {"path": f"p{i}", "input_hash": f"h{i}"}


def test_save_uses_atomic_write_no_tmp_leftover(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    s = PipelineState(target_repo=tmp_path, output_dir=output)
    s.save()
    siblings = [p.name for p in output.iterdir()]
    assert ".designdoc-state.json" in siblings
    assert not any(name.endswith(".tmp") for name in siblings)


def test_save_json_is_valid_after_partial_tmp_left_behind(tmp_path: Path) -> None:
    """If a stale .tmp exists from an earlier crash, save() should still succeed.

    atomic_write's rename replaces the target; the leftover .tmp from a past
    crash is overwritten on the next save's tempfile step."""
    output = tmp_path / "out"
    output.mkdir()
    stale_tmp = output / ".designdoc-state.json.tmp"
    stale_tmp.write_text("GARBAGE")

    s = PipelineState(target_repo=tmp_path, output_dir=output)
    s.save()

    # Target is valid JSON.
    data = json.loads((output / ".designdoc-state.json").read_text())
    assert data["target_repo"] == str(tmp_path)
    # .tmp cleanly replaced (no longer GARBAGE, or gone entirely).
    if stale_tmp.exists():
        # If still present, it must contain the JSON that was about to be renamed.
        assert stale_tmp.read_text() != "GARBAGE"
