"""Stage 3 within-stage resume: per-class checkpoint survives mid-stage crash."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.runner import ClaudeSDKRunner
from designdoc.stages import s1_index, s3_class_docs
from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.state import PipelineState


def _seed(state: PipelineState, classes_per_file: dict[str, list[str]]) -> None:
    """Write stage0 hashes and stage1 signatures with the given classes."""
    hashes = {path: hashlib.sha1(f"src:{path}".encode()).hexdigest() for path in classes_per_file}
    (state.output_dir / STAGE0_FILENAME).write_text(
        json.dumps({"hashes": hashes, "languages": {"python": list(classes_per_file)}})
    )
    sigs = [
        {
            "path": path,
            "classes": [{"name": cls, "methods": [], "bases": []} for cls in classes],
            "functions": [],
            "imports": [],
            "parse_error": None,
        }
        for path, classes in classes_per_file.items()
    ]
    (state.output_dir / s1_index.OUTPUT_FILENAME).write_text(json.dumps(sigs))


class _FakeSDK:
    """Fake SDK that returns valid doer/checker responses and counts calls."""

    def __init__(self) -> None:
        self.call_count: int = 0

    async def query(self, *, prompt: str, options: dict) -> dict:
        self.call_count += 1
        system = options.get("system_prompt") or ""
        if "doc-quality-checker" in system or "documentation QA" in system:
            text = json.dumps({"verdict": "pass", "reasoning": "stub"})
        else:
            text = "# Stub class doc\n\nplaceholder"
        return {
            "text": text,
            "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.0},
        }


def _make_runner(sdk: _FakeSDK) -> ClaudeSDKRunner:
    return ClaudeSDKRunner(budget=CostAccumulator(cap_usd=10.0), sdk=sdk)


@pytest.mark.anyio("asyncio")
async def test_stage3_checkpoints_per_class(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed(state, {"src/foo.py": ["A", "B"]})

    sdk = _FakeSDK()
    await s3_class_docs.run(state=state, runner=_make_runner(sdk), parallelism=1)

    assert "src/foo.py::A" in state.artifact_index
    assert "src/foo.py::B" in state.artifact_index
    assert state.artifact_index["src/foo.py::A"]["input_hash"] != ""


@pytest.mark.anyio("asyncio")
async def test_stage3_rerun_skips_checkpointed_classes(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed(state, {"src/foo.py": ["A", "B"]})

    sdk1 = _FakeSDK()
    await s3_class_docs.run(state=state, runner=_make_runner(sdk1), parallelism=1)
    first_call_count = sdk1.call_count
    assert first_call_count > 0

    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    sdk2 = _FakeSDK()
    await s3_class_docs.run(state=state2, runner=_make_runner(sdk2), parallelism=1)
    assert sdk2.call_count == 0
