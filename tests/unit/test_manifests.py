"""Tests for dependency-manifest parsing."""

from __future__ import annotations

import json
from pathlib import Path

from designdoc.index.manifests import Dep, parse_manifests


def test_pyproject_with_dependencies(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "x"
version = "0.0.1"
dependencies = [
    "requests>=2.31",
    "pydantic>=2.7,<3.0",
    "rich",
]
"""
    )
    deps = parse_manifests(tmp_path)
    names = {d.name for d in deps}
    assert names == {"requests", "pydantic", "rich"}
    req = next(d for d in deps if d.name == "requests")
    assert req.source == "pyproject.toml"
    assert ">=" in req.pinned


def test_requirements_txt(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("flask==2.0.1\n# comment\n-e .\nrequests\n")
    deps = parse_manifests(tmp_path)
    names = {d.name for d in deps}
    assert "flask" in names
    assert "requests" in names


def test_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "x",
                "dependencies": {"react": "^18.0.0"},
                "devDependencies": {"typescript": "^5.3.0"},
            }
        )
    )
    deps = parse_manifests(tmp_path)
    names = {d.name for d in deps}
    assert "react" in names
    assert "typescript" in names


def test_dedupe_by_name_across_manifests(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["requests>=2.31"]\n'
    )
    (tmp_path / "requirements.txt").write_text("requests==2.30\n")
    deps = parse_manifests(tmp_path)
    assert len([d for d in deps if d.name == "requests"]) == 1


def test_empty_repo_returns_empty(tmp_path: Path):
    assert parse_manifests(tmp_path) == []


def test_dep_is_slotted_dataclass():
    d = Dep(name="x", pinned=">=1", source="pyproject.toml")
    # slots=True disables __dict__
    assert not hasattr(d, "__dict__")
