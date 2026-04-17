"""Unit tests for AST-lite signature extraction.

Python signatures are exact (ast-based). TS/JS are regex fallback (best-effort).
Parse errors on one file must not halt the pipeline — we emit a FileSignature
with parse_error set and move on.
"""

from __future__ import annotations

from pathlib import Path

from designdoc.index.signatures import extract_signature


def test_python_module_with_class_and_function(tmp_path: Path):
    src = tmp_path / "mod.py"
    src.write_text(
        '''"""Module docstring."""
from __future__ import annotations
import json

def top(a: int, b: str = "x") -> bool:
    """A top-level function."""
    return True

class Foo:
    """Foo does things."""
    def method(self, x: int) -> None:
        """Method doc."""
        pass
    @staticmethod
    def static_method():
        pass
'''
    )
    sig = extract_signature(src, repo_root=tmp_path)

    assert sig.language == "python"
    assert sig.path == "mod.py"
    assert sig.module_docstring == "Module docstring."
    assert "json" in sig.imports
    assert sig.parse_error is None

    assert len(sig.functions) == 1
    assert sig.functions[0].name == "top"
    # ast.unparse always emits single-quoted strings — match that
    assert sig.functions[0].params == ["a: int", "b: str = 'x'"]
    assert sig.functions[0].docstring == "A top-level function."

    assert len(sig.classes) == 1
    cls = sig.classes[0]
    assert cls.name == "Foo"
    assert cls.docstring == "Foo does things."
    assert [m.name for m in cls.methods] == ["method", "static_method"]
    assert cls.methods[0].docstring == "Method doc."


def test_python_inheritance_captured(tmp_path: Path):
    src = tmp_path / "child.py"
    src.write_text(
        """
class Parent:
    pass

class Child(Parent, object):
    pass
"""
    )
    sig = extract_signature(src, repo_root=tmp_path)
    parent = next(c for c in sig.classes if c.name == "Parent")
    child = next(c for c in sig.classes if c.name == "Child")
    assert parent.bases == []
    assert child.bases == ["Parent", "object"]


def test_python_syntax_error_emits_parse_error(tmp_path: Path):
    src = tmp_path / "broken.py"
    src.write_text("def oops( this is not valid python\n")
    sig = extract_signature(src, repo_root=tmp_path)
    assert sig.language == "python"
    assert sig.parse_error is not None
    assert sig.classes == []
    assert sig.functions == []


def test_empty_python_file(tmp_path: Path):
    src = tmp_path / "empty.py"
    src.write_text("")
    sig = extract_signature(src, repo_root=tmp_path)
    assert sig.language == "python"
    assert sig.parse_error is None
    assert sig.classes == []
    assert sig.functions == []
    assert sig.module_docstring is None


def test_typescript_regex_fallback_finds_class(tmp_path: Path):
    src = tmp_path / "x.ts"
    src.write_text(
        """
import { foo } from "bar";

export class Widget {
    constructor(private x: number) {}
    greet(): string { return "hi"; }
}

export function helper(a: number): number { return a; }
"""
    )
    sig = extract_signature(src, repo_root=tmp_path)
    assert sig.language == "typescript"
    assert sig.parse_error is None
    assert any(c.name == "Widget" for c in sig.classes)
    assert any(f.name == "helper" for f in sig.functions)


def test_unknown_language_raises(tmp_path: Path):
    src = tmp_path / "foo.bin"
    src.write_text("binary")
    try:
        extract_signature(src, repo_root=tmp_path)
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "unsupported" in str(e).lower() or "unknown" in str(e).lower()


def test_signature_is_json_serializable(tmp_path: Path):
    src = tmp_path / "x.py"
    src.write_text("class A:\n    pass\n")
    sig = extract_signature(src, repo_root=tmp_path)
    d = sig.to_dict()
    assert d["language"] == "python"
    assert d["classes"][0]["name"] == "A"
