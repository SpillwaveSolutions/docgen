"""Path-traversal guard for s3._class_doc_path (T5.2 from issue #18).

Defense-in-depth: even though the function's design (taking only the last
directory component of source_path's parent for `pkg`) accidentally
neutralizes ``../`` in source_path, a maliciously-constructed class_name
or pkg WOULD escape via path joining (which doesn't normalize until
``.resolve()``). The explicit guard inside ``_class_doc_path`` raises
ValueError on any escape, regardless of the attack vector.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from designdoc.stages.s3_class_docs import _class_doc_path


def test_happy_path_lands_under_packages(tmp_path: Path) -> None:
    out = _class_doc_path(tmp_path, "src/payments/gateway.py", "Gateway")
    packages_root = (tmp_path / "packages").resolve()
    assert out.resolve().is_relative_to(packages_root)
    # Last directory component is the "package" — "src" is stripped.
    assert "payments" in out.parts


def test_root_source_path_falls_back_to_root_pkg(tmp_path: Path) -> None:
    """Files at the source root get pkg='root' — still under packages/."""
    out = _class_doc_path(tmp_path, "module.py", "Foo")
    packages_root = (tmp_path / "packages").resolve()
    assert out.resolve().is_relative_to(packages_root)
    assert "root" in out.parts


def test_source_path_with_traversal_neutralized_by_design(tmp_path: Path) -> None:
    """Source-path traversal is neutralized by design — only the last directory
    component is used for the package name. ``../../etc/passwd.py`` becomes
    pkg='etc', file lands under packages/etc/classes/Evil.md. Documents the
    contract; the explicit guard remains as defense-in-depth for
    class_name-based attacks (next test)."""
    out = _class_doc_path(tmp_path, "../../etc/passwd.py", "Evil")
    packages_root = (tmp_path / "packages").resolve()
    assert out.resolve().is_relative_to(packages_root)
    assert "etc" in out.parts


def test_class_name_with_shallow_traversal_lands_inside_packages(tmp_path: Path) -> None:
    """A shallow class_name traversal like ``../../etc/passwd`` resolves to
    ``packages/etc/passwd.md`` — still under packages/, so the guard
    correctly does NOT fire. This documents the boundary."""
    out = _class_doc_path(tmp_path, "src/foo.py", "../../etc/passwd")
    packages_root = (tmp_path / "packages").resolve()
    assert out.resolve().is_relative_to(packages_root)


def test_deeply_nested_class_name_traversal_rejected(tmp_path: Path) -> None:
    """A class_name containing enough ``../`` steps to escape ``packages/``
    triggers the guard. From ``packages/<pkg>/classes/`` it takes 3 steps
    to escape; this attack vector isn't reachable in production (class_name
    comes from Python AST, restricted to valid identifiers) but the guard
    is cheap defense for any future caller that forwards untrusted strings."""
    with pytest.raises(ValueError, match="escapes"):
        _class_doc_path(tmp_path, "src/foo.py", "../../../tmp/anywhere")
