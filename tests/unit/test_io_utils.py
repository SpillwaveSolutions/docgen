"""Atomic write helper: tempfile-and-rename semantics under POSIX."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from designdoc.io_utils import atomic_write, sha1_keyed


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


# ---------------------------------------------------------------------------
# sha1_keyed: stable SHA1 over dict items sorted by key.
# ---------------------------------------------------------------------------


def _legacy_hash(items: dict[str, str]) -> str:
    """Reference implementation matching the pre-refactor _hash_* helpers.

    Used to lock in byte-identical hashes so existing state.json/artifact_index
    entries are not invalidated by the refactor.
    """
    h = hashlib.sha1()
    for key in sorted(items):
        h.update(key.encode("utf-8"))
        h.update(b"\0")
        h.update(items[key].encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def test_sha1_keyed_is_deterministic() -> None:
    items = {"foo.md": "hello", "bar.md": "world"}
    assert sha1_keyed(items) == sha1_keyed(items)


def test_sha1_keyed_is_order_independent() -> None:
    a = {"a": "1", "b": "2"}
    b = {"b": "2", "a": "1"}
    assert sha1_keyed(a) == sha1_keyed(b)


def test_sha1_keyed_is_content_sensitive() -> None:
    base = {"a": "1", "b": "2"}
    changed_value = {"a": "1", "b": "3"}
    changed_key = {"a": "1", "c": "2"}
    assert sha1_keyed(base) != sha1_keyed(changed_value)
    assert sha1_keyed(base) != sha1_keyed(changed_key)


def test_sha1_keyed_empty_dict_is_empty_sha1() -> None:
    # Empty input hashes to the SHA1 of the empty string, matching the
    # pre-refactor helpers (the for-loop simply never runs).
    assert sha1_keyed({}) == hashlib.sha1().hexdigest()


def test_sha1_keyed_matches_legacy_single_pair() -> None:
    """Guards against the refactor accidentally changing hash bytes for the
    single-key case the way _hash_class_docs / _hash_readmes used it."""
    items = {"only.md": "content"}
    assert sha1_keyed(items) == _legacy_hash(items)


def test_sha1_keyed_matches_legacy_multi_pair() -> None:
    items = {
        "alpha": "one",
        "beta": "two",
        "gamma": "three with\nnewlines and \0 nulls",
    }
    assert sha1_keyed(items) == _legacy_hash(items)


def test_sha1_keyed_matches_legacy_dep_triple_encoding() -> None:
    """_hash_dep / _hash_deps emitted name\\0pinned\\0source\\n per row.

    The refactor encodes each row as a dict entry whose value is
    f"{pinned}\\0{source}"; this test locks in byte-identical output.
    """
    # Legacy s6 per-dep encoding, inlined.
    legacy = hashlib.sha1()
    legacy.update(b"requests")
    legacy.update(b"\0")
    legacy.update(b">=2.31")
    legacy.update(b"\0")
    legacy.update(b"pyproject.toml")
    legacy.update(b"\n")
    expected = legacy.hexdigest()

    via_sha1_keyed = sha1_keyed({"requests": ">=2.31\0pyproject.toml"})
    assert via_sha1_keyed == expected
