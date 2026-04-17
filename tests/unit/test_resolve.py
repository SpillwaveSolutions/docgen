"""Unit tests for HIL resolve helpers (emit_questions + apply_fix)."""

from __future__ import annotations

from pathlib import Path

from designdoc.hil import HILIssue, append_issue, inline_comment
from designdoc.resolve import apply_fix, emit_questions, load_hil_yaml


def _seed_hil(output_dir: Path, artifact_rel: str = "docs/x.md") -> Path:
    """Create a minimal artifact + hil-issues.yaml referencing it."""
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / artifact_rel
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        f"# Artifact\n\n{inline_comment('HIL-001', 'retry policy disputed')}\nOriginal stub.\n"
    )
    hil_yaml = output_dir / "hil-issues.yaml"
    append_issue(
        hil_yaml,
        HILIssue(
            id="HIL-001",
            artifact=artifact_rel,
            stage="class_docs",
            severity="major",
            doer_said="claims retries are bounded",
            checker_said="no retry cap in source",
            attempts=3,
            status="open",
            suggested_fixes=["confirm retry cap", "re-read lines 110-140"],
        ),
    )
    return hil_yaml


def test_emit_questions_returns_first_open_issue(tmp_path: Path):
    out = tmp_path / "design"
    hil_yaml = _seed_hil(out)

    q = emit_questions(hil_yaml, out)
    assert q["id"] == "HIL-001"
    assert q["stage"] == "class_docs"
    assert q["severity"] == "major"
    assert q["suggested_fixes"] == ["confirm retry cap", "re-read lines 110-140"]
    assert q["artifact_exists"] is True


def test_emit_questions_returns_none_when_all_resolved(tmp_path: Path):
    out = tmp_path / "design"
    hil_yaml = _seed_hil(out)
    doc = load_hil_yaml(hil_yaml)
    doc["issues"][0]["status"] = "resolved"
    from designdoc.resolve import save_hil_yaml

    save_hil_yaml(hil_yaml, doc)

    q = emit_questions(hil_yaml, out)
    assert q == {"status": "none-open", "unresolved_count": 0}


def test_emit_questions_no_yaml_file(tmp_path: Path):
    out = tmp_path / "design"
    out.mkdir(parents=True)
    q = emit_questions(out / "hil-issues.yaml", out)
    assert q["status"] == "none-open"


def test_apply_fix_replaces_inline_hil_comment(tmp_path: Path):
    out = tmp_path / "design"
    hil_yaml = _seed_hil(out)
    artifact = out / "docs" / "x.md"
    assert "HIL-001" in artifact.read_text()

    result = apply_fix(
        hil_yaml, out, hil_id="HIL-001", fix_text="Retries are capped at 3 attempts."
    )
    assert result["applied"] is True
    body = artifact.read_text()
    assert "HIL-001" not in body  # HIL comment replaced
    assert "Retries are capped at 3 attempts." in body

    # YAML updated
    doc = load_hil_yaml(hil_yaml)
    assert doc["issues"][0]["status"] == "resolved"
    assert doc["unresolved_count"] == 0


def test_apply_fix_unknown_id_reports_not_found(tmp_path: Path):
    out = tmp_path / "design"
    _seed_hil(out)
    result = apply_fix(out / "hil-issues.yaml", out, hil_id="HIL-999", fix_text="x")
    assert result["applied"] is False
    assert "not found" in result["reason"]


def test_apply_fix_missing_artifact_reports_error(tmp_path: Path):
    out = tmp_path / "design"
    hil_yaml = _seed_hil(out, artifact_rel="docs/gone.md")
    (out / "docs" / "gone.md").unlink()

    result = apply_fix(hil_yaml, out, hil_id="HIL-001", fix_text="x")
    assert result["applied"] is False
    assert "missing" in result["reason"]
