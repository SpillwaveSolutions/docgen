"""Tests for per-file content hashing in Stage 0's DiscoveryReport.

Hashes are the groundwork for incremental regeneration (v1.1 step 1). This
PR only adds and persists them — Stages 2-7 start consuming them in a
follow-up PR. Changing this file's API is a breaking change for that work.
"""

from __future__ import annotations

from pathlib import Path

from designdoc.index.discover import discover


def _mk(root: Path, *pairs: tuple[str, str]) -> None:
    for rel, content in pairs:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def test_discover_report_includes_hash_per_file(tmp_path: Path):
    _mk(tmp_path, ("a.py", "x = 1\n"), ("b.py", "y = 2\n"))
    report = discover(tmp_path)

    # hashes is a dict keyed by the same posix-style relative path the tree uses
    assert set(report.hashes.keys()) == {"a.py", "b.py"}
    # Each hash is a lowercase hex SHA1 (40 chars)
    for h in report.hashes.values():
        assert len(h) == 40
        assert all(c in "0123456789abcdef" for c in h)


def test_same_content_produces_same_hash(tmp_path: Path):
    """Two files with identical bytes must hash identically — incremental
    regeneration relies on this."""
    _mk(tmp_path, ("a.py", "print('hello')\n"), ("b.py", "print('hello')\n"))
    report = discover(tmp_path)
    assert report.hashes["a.py"] == report.hashes["b.py"]


def test_different_content_produces_different_hash(tmp_path: Path):
    _mk(tmp_path, ("a.py", "print('hello')\n"), ("b.py", "print('world')\n"))
    report = discover(tmp_path)
    assert report.hashes["a.py"] != report.hashes["b.py"]


def test_hash_changes_when_file_content_changes(tmp_path: Path):
    _mk(tmp_path, ("a.py", "v = 1\n"))
    h1 = discover(tmp_path).hashes["a.py"]
    _mk(tmp_path, ("a.py", "v = 2\n"))
    h2 = discover(tmp_path).hashes["a.py"]
    assert h1 != h2


def test_hashes_in_serialized_report(tmp_path: Path):
    _mk(tmp_path, ("a.py", "x = 1\n"))
    d = discover(tmp_path).to_dict()
    assert "hashes" in d
    assert "a.py" in d["hashes"]


def test_empty_repo_has_empty_hashes(tmp_path: Path):
    report = discover(tmp_path)
    assert report.hashes == {}


def test_hashes_align_with_tree(tmp_path: Path):
    """Every entry in tree must have a hash, and no extras."""
    _mk(tmp_path, ("a.py", "1\n"), ("sub/b.py", "2\n"), ("README.md", "ignored\n"))
    report = discover(tmp_path)
    tree_paths = {p.as_posix() for p in report.tree}
    assert set(report.hashes.keys()) == tree_paths
