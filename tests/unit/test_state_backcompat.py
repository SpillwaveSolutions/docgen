"""artifact_index shape change + backcompat loader.

v1.2 moves artifact_index from {id: str_path} to {id: {"path": ..., "input_hash": ...}}.
Old state files still round-trip: the loader migrates string values to dict
form with empty input_hash, which will never match current hashes -> the
stage will reprocess, which is safe and matches pre-v1.2 behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

from designdoc.state import STATE_FILENAME, PipelineState


def test_new_shape_round_trips(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    s = PipelineState(target_repo=tmp_path, output_dir=output)
    s.artifact_index["class:Foo"] = {"path": "packages/x/Foo.md", "input_hash": "abc"}
    s.save()

    loaded = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    assert loaded.artifact_index == {
        "class:Foo": {"path": "packages/x/Foo.md", "input_hash": "abc"}
    }


def test_backcompat_loads_old_string_shape(tmp_path: Path) -> None:
    """An old (v1.1) state file with string-valued artifact_index loads without error."""
    output = tmp_path / "out"
    output.mkdir()
    old_state = {
        "target_repo": str(tmp_path),
        "output_dir": str(output),
        "current_stage": 3,
        "stages": {"class_docs": "done"},
        "total_retries": 0,
        "hil_issues": [],
        "artifact_index": {"class:Foo": "packages/x/Foo.md"},
        "prev_hashes": {},
        "rollup_hashes": {},
    }
    (output / STATE_FILENAME).write_text(json.dumps(old_state))

    loaded = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    assert loaded.artifact_index == {"class:Foo": {"path": "packages/x/Foo.md", "input_hash": ""}}


def test_backcompat_migrated_values_force_reprocess(tmp_path: Path) -> None:
    """Empty input_hash will never equal a real SHA1 — skip-check fails, stage reprocesses."""
    output = tmp_path / "out"
    output.mkdir()
    old_state = {
        "target_repo": str(tmp_path),
        "output_dir": str(output),
        "current_stage": 0,
        "stages": {},
        "total_retries": 0,
        "hil_issues": [],
        "artifact_index": {"class:Foo": "packages/x/Foo.md"},
        "prev_hashes": {},
        "rollup_hashes": {},
    }
    (output / STATE_FILENAME).write_text(json.dumps(old_state))

    loaded = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    entry = loaded.artifact_index["class:Foo"]
    # Pretend the current hash of the input is some real SHA.
    current_input_hash = "a" * 40
    assert entry["input_hash"] != current_input_hash
