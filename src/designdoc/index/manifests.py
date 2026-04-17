"""Dependency-manifest parsing.

Supports: pyproject.toml (PEP 621), requirements.txt, package.json. More
manifest types can be added as needed.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Dep:
    name: str
    pinned: str
    source: str  # filename it came from


_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*([<>=!~^]=?\s*\S+)?")


def parse_manifests(repo_root: Path) -> list[Dep]:
    """Collect direct dependencies from every recognized manifest in the repo.

    Only direct deps — transitives are filtered out by the manifest format
    itself (pyproject [project.dependencies], package.json "dependencies", etc).
    """
    deps: list[Dep] = []

    for py in [repo_root / "pyproject.toml"]:
        if py.exists():
            deps.extend(_parse_pyproject(py))

    for req in sorted(repo_root.glob("requirements*.txt")):
        deps.extend(_parse_requirements(req))

    for pkg in [repo_root / "package.json"]:
        if pkg.exists():
            deps.extend(_parse_package_json(pkg))

    # De-dupe by (name) — keep the first occurrence
    seen: set[str] = set()
    unique: list[Dep] = []
    for d in deps:
        if d.name in seen:
            continue
        seen.add(d.name)
        unique.append(d)
    return unique


def _parse_pyproject(path: Path) -> list[Dep]:
    data = tomllib.loads(path.read_text())
    project = data.get("project", {})
    deps: list[Dep] = []
    for entry in project.get("dependencies", []):
        m = _REQ_LINE.match(entry)
        if not m:
            continue
        name = m.group(1)
        pinned = (m.group(2) or "").strip()
        deps.append(Dep(name=name, pinned=pinned, source="pyproject.toml"))
    return deps


def _parse_requirements(path: Path) -> list[Dep]:
    deps: list[Dep] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _REQ_LINE.match(line)
        if not m:
            continue
        deps.append(Dep(name=m.group(1), pinned=(m.group(2) or "").strip(), source=path.name))
    return deps


def _parse_package_json(path: Path) -> list[Dep]:
    data = json.loads(path.read_text())
    deps: list[Dep] = []
    for section in ("dependencies", "devDependencies"):
        for name, ver in (data.get(section) or {}).items():
            deps.append(Dep(name=name, pinned=str(ver), source="package.json"))
    return deps
