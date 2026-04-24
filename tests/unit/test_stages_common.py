"""Tests for the shared stage helper `current_source_hashes`.

This helper is consumed by Stages 2 and 3 to load the per-source SHA1 map
that Stage 0 persisted to stage0_discovery.json. It must return an empty
dict on every failure mode (missing file, malformed JSON, I/O error,
missing `hashes` key, null `hashes` value) — that's what lets downstream
stages treat every file as "changed" and reprocess on a corrupt discovery
artifact rather than crashing the pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

from designdoc.stages._common import current_source_hashes
from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.state import PipelineState


def _mk_state(tmp_path: Path) -> PipelineState:
    repo = tmp_path / "repo"
    out = tmp_path / "out"
    repo.mkdir()
    out.mkdir()
    return PipelineState(target_repo=repo, output_dir=out)


def test_returns_hashes_when_stage0_present(tmp_path: Path):
    state = _mk_state(tmp_path)
    (state.output_dir / STAGE0_FILENAME).write_text(
        json.dumps({"hashes": {"a.py": "abc123", "b.py": "def456"}})
    )
    assert current_source_hashes(state) == {"a.py": "abc123", "b.py": "def456"}


def test_returns_empty_when_stage0_missing(tmp_path: Path):
    state = _mk_state(tmp_path)
    assert current_source_hashes(state) == {}


def test_returns_empty_on_malformed_json(tmp_path: Path):
    state = _mk_state(tmp_path)
    (state.output_dir / STAGE0_FILENAME).write_text("{ not json")
    assert current_source_hashes(state) == {}


def test_returns_empty_when_hashes_key_absent(tmp_path: Path):
    state = _mk_state(tmp_path)
    (state.output_dir / STAGE0_FILENAME).write_text(json.dumps({"tree": []}))
    assert current_source_hashes(state) == {}


def test_returns_empty_when_hashes_value_is_null(tmp_path: Path):
    state = _mk_state(tmp_path)
    (state.output_dir / STAGE0_FILENAME).write_text(json.dumps({"hashes": None}))
    assert current_source_hashes(state) == {}


def test_returns_empty_when_hashes_value_is_empty_dict(tmp_path: Path):
    state = _mk_state(tmp_path)
    (state.output_dir / STAGE0_FILENAME).write_text(json.dumps({"hashes": {}}))
    assert current_source_hashes(state) == {}
