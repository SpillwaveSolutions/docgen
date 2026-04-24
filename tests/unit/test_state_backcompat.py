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


def test_backcompat_total_retries_maps_to_doer_content_retries(tmp_path: Path) -> None:
    """Old state.json had total_retries; new split is doer_content_retries +
    checker_parse_retries. Since pre-split runs only ever incremented on the
    doer-content path (that was the dominant case), the old value reads into
    doer_content_retries and checker_parse_retries defaults to 0."""
    output = tmp_path / "out"
    output.mkdir()
    old_state = {
        "target_repo": str(tmp_path),
        "output_dir": str(output),
        "current_stage": 3,
        "stages": {"class_docs": "done"},
        "total_retries": 5,
        "hil_issues": [],
        "artifact_index": {},
        "prev_hashes": {},
        "rollup_hashes": {},
    }
    (output / STATE_FILENAME).write_text(json.dumps(old_state))

    loaded = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    assert loaded.doer_content_retries == 5
    assert loaded.checker_parse_retries == 0


def test_backcompat_new_fields_win_over_legacy_total_retries(tmp_path: Path) -> None:
    """If a state file has both the new fields and legacy total_retries (e.g.
    a mid-migration artifact), the new fields take precedence — legacy value
    is ignored, not double-counted."""
    output = tmp_path / "out"
    output.mkdir()
    mixed_state = {
        "target_repo": str(tmp_path),
        "output_dir": str(output),
        "current_stage": 3,
        "stages": {"class_docs": "done"},
        "total_retries": 99,
        "doer_content_retries": 3,
        "checker_parse_retries": 2,
        "hil_issues": [],
        "artifact_index": {},
        "prev_hashes": {},
        "rollup_hashes": {},
    }
    (output / STATE_FILENAME).write_text(json.dumps(mixed_state))

    loaded = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    assert loaded.doer_content_retries == 3
    assert loaded.checker_parse_retries == 2
