"""AST-lite signature extraction.

For Python, uses the standard `ast` module — exact, fast, no LLM.
For TypeScript/JavaScript, uses regex fallback (tree-sitter is v2).

A SyntaxError in any file emits a FileSignature with `parse_error` set
rather than halting the pipeline. Broken source is exactly the kind of repo
most in need of design docs.
"""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from designdoc.index.discover import EXT_TO_LANG


@dataclass
class FunctionSignature:
    name: str
    params: list[str] = field(default_factory=list)
    docstring: str | None = None


@dataclass
class ClassSignature:
    name: str
    bases: list[str] = field(default_factory=list)
    docstring: str | None = None
    methods: list[FunctionSignature] = field(default_factory=list)


@dataclass
class FileSignature:
    language: str
    path: str
    module_docstring: str | None = None
    imports: list[str] = field(default_factory=list)
    classes: list[ClassSignature] = field(default_factory=list)
    functions: list[FunctionSignature] = field(default_factory=list)
    parse_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def extract_signature(path: Path, *, repo_root: Path) -> FileSignature:
    """Extract a signature for one file. Dispatches by language."""
    lang = EXT_TO_LANG.get(path.suffix.lower())
    if lang is None:
        raise ValueError(f"unsupported file extension: {path.suffix}")

    rel = path.relative_to(repo_root).as_posix()
    source = path.read_text(errors="replace")

    if lang == "python":
        return _extract_python(source, rel)
    if lang in ("typescript", "javascript"):
        return _extract_js_like(source, rel, language=lang)

    # Languages we classify but don't yet parse — emit an empty skeleton
    return FileSignature(language=lang, path=rel)


def _extract_python(source: str, path: str) -> FileSignature:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return FileSignature(language="python", path=path, parse_error=str(e))

    sig = FileSignature(language="python", path=path)
    sig.module_docstring = ast.get_docstring(tree)

    for node in tree.body:
        if isinstance(node, ast.Import):
            sig.imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            sig.imports.append(node.module)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            sig.functions.append(_py_function(node))
        elif isinstance(node, ast.ClassDef):
            sig.classes.append(_py_class(node))

    return sig


def _py_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionSignature:
    params: list[str] = []
    args = node.args
    for arg in args.args:
        params.append(_format_arg(arg, _default_for(args, arg)))
    if args.vararg:
        params.append(f"*{args.vararg.arg}")
    for arg in args.kwonlyargs:
        params.append(_format_arg(arg, _default_for(args, arg)))
    if args.kwarg:
        params.append(f"**{args.kwarg.arg}")
    return FunctionSignature(
        name=node.name,
        params=params,
        docstring=ast.get_docstring(node),
    )


def _py_class(node: ast.ClassDef) -> ClassSignature:
    methods = [
        _py_function(child)
        for child in node.body
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    bases = [ast.unparse(b) for b in node.bases]
    return ClassSignature(
        name=node.name,
        bases=bases,
        docstring=ast.get_docstring(node),
        methods=methods,
    )


def _format_arg(arg: ast.arg, default: ast.expr | None) -> str:
    rendered = arg.arg
    if arg.annotation is not None:
        rendered += f": {ast.unparse(arg.annotation)}"
    if default is not None:
        rendered += f" = {ast.unparse(default)}"
    return rendered


def _default_for(args: ast.arguments, arg: ast.arg) -> ast.expr | None:
    """Return the default expression for `arg` within positional or keyword-only args."""
    if arg in args.args:
        i = args.args.index(arg)
        offset = len(args.args) - len(args.defaults)
        if i >= offset:
            return args.defaults[i - offset]
        return None
    if arg in args.kwonlyargs:
        i = args.kwonlyargs.index(arg)
        default = args.kw_defaults[i]
        return default
    return None


# --- TypeScript / JavaScript regex fallback -------------------------------------

_TS_CLASS_RE = re.compile(r"(?:export\s+)?class\s+(\w+)(?:\s+extends\s+([\w.]+))?", re.MULTILINE)
_TS_FUNC_RE = re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE)
_TS_IMPORT_RE = re.compile(r'^\s*import\s+.+?\s+from\s+["\']([^"\']+)["\']', re.MULTILINE)
_TS_METHOD_RE = re.compile(r"^\s+(?:public|private|protected|async|static)?\s*(\w+)\s*\(")


def _extract_js_like(source: str, path: str, *, language: str) -> FileSignature:
    imports = _TS_IMPORT_RE.findall(source)
    functions = [
        FunctionSignature(
            name=m.group(1), params=[p.strip() for p in m.group(2).split(",") if p.strip()]
        )
        for m in _TS_FUNC_RE.finditer(source)
    ]

    classes: list[ClassSignature] = []
    for m in _TS_CLASS_RE.finditer(source):
        name = m.group(1)
        base = m.group(2)
        classes.append(
            ClassSignature(
                name=name,
                bases=[base] if base else [],
                methods=_parse_js_methods_after(source, m.end()),
            )
        )

    return FileSignature(
        language=language,
        path=path,
        imports=imports,
        classes=classes,
        functions=functions,
    )


def _parse_js_methods_after(source: str, start: int) -> list[FunctionSignature]:
    """Extract method names inside the class body starting at `start`.

    Best-effort: finds the nearest `{`, walks to its matching `}`, then scans
    for indented `name(` patterns. Handles one level of brace nesting.
    """
    brace = source.find("{", start)
    if brace == -1:
        return []
    depth = 1
    i = brace + 1
    while i < len(source) and depth > 0:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    body = source[brace + 1 : i - 1]
    seen: set[str] = set()
    methods: list[FunctionSignature] = []
    for m in _TS_METHOD_RE.finditer(body):
        name = m.group(1)
        if name in {"constructor", "if", "for", "while", "return", "function"} - {"constructor"}:
            continue
        if name in seen:
            continue
        seen.add(name)
        methods.append(FunctionSignature(name=name))
    return methods
