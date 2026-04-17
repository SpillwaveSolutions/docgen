"""Language detection and file-tree discovery.

No LLM. Deterministic. Walks the repo once, classifies each file by extension,
and returns a DiscoveryReport that downstream stages consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXCLUDES: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "target",
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)


EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
}


@dataclass
class DiscoveryReport:
    languages: dict[str, int] = field(default_factory=dict)
    tree: list[Path] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "languages": dict(self.languages),
            "tree": [str(p) for p in self.tree],
        }


def discover(
    repo_root: Path,
    exclude_paths: list[str] | None = None,
) -> DiscoveryReport:
    """Walk repo_root and classify each file by extension.

    exclude_paths are matched as substring components of the path — e.g.
    "skip" excludes any file under a directory named "skip" anywhere in the tree.
    """
    user_excludes = set(exclude_paths or [])
    excludes = DEFAULT_EXCLUDES | user_excludes

    languages: dict[str, int] = {}
    tree: list[Path] = []

    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in excludes for part in path.parts):
            continue
        lang = EXT_TO_LANG.get(path.suffix.lower())
        if lang is None:
            continue
        rel = path.relative_to(repo_root)
        tree.append(rel)
        languages[lang] = languages.get(lang, 0) + 1

    return DiscoveryReport(languages=languages, tree=tree)
