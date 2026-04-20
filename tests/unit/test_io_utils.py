"""Atomic write helper: tempfile-and-rename semantics under POSIX."""

from __future__ import annotations

from pathlib import Path

import pytest

from designdoc.io_utils import atomic_write


def test_atomic_write_creates_target(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    atomic_write(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    target.write_text("old")
    atomic_write(target, "new")
    assert target.read_text() == "new"


def test_atomic_write_leaves_no_tmp_on_success(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    atomic_write(target, "hello")
    tmp_siblings = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert tmp_siblings == []


def test_atomic_write_requires_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "out.md"
    with pytest.raises(FileNotFoundError):
        atomic_write(target, "hello")


def test_atomic_write_tmp_is_gone_after_replace(tmp_path: Path) -> None:
    """The .tmp file must not persist after the rename."""
    target = tmp_path / "out.md"
    atomic_write(target, "hello")
    tmp = target.with_suffix(target.suffix + ".tmp")
    assert not tmp.exists()
    assert target.exists()
