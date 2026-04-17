"""Unit tests for the discover module.

discover(repo_root) returns a DiscoveryReport with:
- languages: dict[str, int]  — count of files per detected language
- tree: list[Path]            — every included file, paths relative to repo_root

Excludes: node_modules, .venv, venv, dist, build, target, .git, plus any
paths passed via exclude_paths.
"""

from __future__ import annotations

from pathlib import Path

from designdoc.index.discover import DEFAULT_EXCLUDES, DiscoveryReport, discover


def _mk(root: Path, *rels: str) -> None:
    for r in rels:
        p = root / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# test\n")


def test_detects_python_files(tmp_path: Path):
    _mk(tmp_path, "a.py", "pkg/b.py")
    report = discover(tmp_path)
    assert report.languages == {"python": 2}
    assert Path("a.py") in report.tree
    assert Path("pkg/b.py") in report.tree


def test_detects_multiple_languages(tmp_path: Path):
    _mk(
        tmp_path,
        "src/app.py",
        "web/index.ts",
        "web/util.tsx",
        "server/main.go",
        "lib/core.rs",
    )
    report = discover(tmp_path)
    assert report.languages["python"] == 1
    assert report.languages["typescript"] == 2
    assert report.languages["go"] == 1
    assert report.languages["rust"] == 1


def test_excludes_defaults(tmp_path: Path):
    _mk(tmp_path, "real.py", "node_modules/x.js", ".venv/lib/python3.12/site-packages/foo.py")
    report = discover(tmp_path)
    assert report.tree == [Path("real.py")]
    assert "python" in report.languages
    assert report.languages["python"] == 1


def test_excludes_custom_paths(tmp_path: Path):
    _mk(tmp_path, "keep.py", "skip/me.py", "also/skip.py")
    report = discover(tmp_path, exclude_paths=["skip", "also"])
    assert report.tree == [Path("keep.py")]


def test_excludes_dot_git(tmp_path: Path):
    _mk(tmp_path, "a.py", ".git/HEAD")
    report = discover(tmp_path)
    assert Path("a.py") in report.tree
    assert all(".git" not in p.parts for p in report.tree)


def test_empty_repo_returns_empty_report(tmp_path: Path):
    report = discover(tmp_path)
    assert report.languages == {}
    assert report.tree == []


def test_default_excludes_list_is_documented():
    """Any changes to the default exclude list should be explicit (tested)."""
    assert "node_modules" in DEFAULT_EXCLUDES
    assert ".venv" in DEFAULT_EXCLUDES
    assert ".git" in DEFAULT_EXCLUDES
    assert "build" in DEFAULT_EXCLUDES
    assert "target" in DEFAULT_EXCLUDES


def test_unknown_extensions_ignored(tmp_path: Path):
    _mk(tmp_path, "a.py", "image.png", "data.bin", "README.md")
    report = discover(tmp_path)
    assert report.tree == [Path("a.py")]
    assert "python" in report.languages


def test_discover_report_is_serializable(tmp_path: Path):
    _mk(tmp_path, "a.py")
    report = discover(tmp_path)
    assert isinstance(report, DiscoveryReport)
    # to_dict must be JSON-round-trippable for state persistence
    d = report.to_dict()
    assert d["languages"] == {"python": 1}
    assert d["tree"] == ["a.py"]
