"""Integration tests for `designdoc resolve` subprocess invocations."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from designdoc.hil import HILIssue, append_issue, inline_comment


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "designdoc", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _seed(output: Path) -> None:
    (output / "docs").mkdir(parents=True, exist_ok=True)
    (output / "docs" / "x.md").write_text(
        f"# X\n\n{inline_comment('HIL-001', 'disputed claim')}\nOriginal stub.\n"
    )
    append_issue(
        output / "hil-issues.yaml",
        HILIssue(
            id="HIL-001",
            artifact="docs/x.md",
            stage="class_docs",
            severity="major",
            doer_said="stubbed claim",
            checker_said="unsupported by source",
            attempts=3,
            status="open",
            suggested_fixes=["re-read source"],
        ),
    )


def test_resolve_emit_questions_prints_first_open_issue(tmp_path: Path):
    output = tmp_path / "design"
    _seed(output)
    result = _run(["resolve", "--output", str(output), "--repo", str(tmp_path), "--emit-questions"])
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["id"] == "HIL-001"
    assert data["artifact"] == "docs/x.md"
    assert data["suggested_fixes"] == ["re-read source"]


def test_resolve_apply_fix_patches_doc_and_marks_resolved(tmp_path: Path):
    output = tmp_path / "design"
    _seed(output)

    result = _run(
        [
            "resolve",
            "--output",
            str(output),
            "--repo",
            str(tmp_path),
            "--apply-fix",
            "HIL-001",
            "--fix",
            "Clarified: stubbed claim is accurate per line 42.",
        ]
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["applied"] is True

    body = (output / "docs" / "x.md").read_text()
    assert "HIL-001" not in body
    assert "Clarified" in body


def test_resolve_missing_args_exits_nonzero(tmp_path: Path):
    result = _run(["resolve", "--output", str(tmp_path), "--repo", str(tmp_path)])
    assert result.returncode != 0
    assert "--emit-questions" in (result.stdout + (result.stderr or ""))
