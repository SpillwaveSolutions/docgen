"""Tests for prev_hashes on PipelineState.

prev_hashes is the seed from the previous SUCCESSFUL run. Stage 8 promotes
the current run's hashes into prev_hashes at finalize. Incremental stages
(landing in later PRs) compare Stage 0's current hashes against prev_hashes
to decide what to regenerate.
"""

from __future__ import annotations

from pathlib import Path

from designdoc.state import PipelineState


def test_new_state_has_empty_prev_hashes(tmp_path: Path):
    s = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    assert s.prev_hashes == {}


def test_prev_hashes_roundtrip(tmp_path: Path):
    s = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    s.prev_hashes = {"a.py": "hash1", "sub/b.py": "hash2"}
    s.save()

    s2 = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    assert s2.prev_hashes == {"a.py": "hash1", "sub/b.py": "hash2"}


def test_unchanged_paths_returns_paths_whose_hash_still_matches(tmp_path: Path):
    s = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    s.prev_hashes = {"a.py": "h1", "b.py": "h2", "c.py": "h3"}

    current = {"a.py": "h1", "b.py": "changed", "c.py": "h3", "new.py": "h4"}

    unchanged = s.unchanged_paths(current)
    assert unchanged == {"a.py", "c.py"}


def test_unchanged_paths_empty_when_no_prev(tmp_path: Path):
    """First run has no prev_hashes — nothing is 'unchanged', everything regenerates."""
    s = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    current = {"a.py": "h1"}
    assert s.unchanged_paths(current) == set()


def test_unchanged_paths_ignores_deleted_files(tmp_path: Path):
    """Files in prev_hashes but absent from current are deleted — not 'unchanged'."""
    s = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    s.prev_hashes = {"gone.py": "h1", "still.py": "h2"}
    assert s.unchanged_paths({"still.py": "h2"}) == {"still.py"}
