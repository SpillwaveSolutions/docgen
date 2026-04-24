"""HIL resolution helpers for the /designdoc resolve plugin flow.

Plugin-driven: Claude reads hil-issues.yaml via the Bash CLI, asks the user
via AskUserQuestion, then tells the CLI to apply the chosen fix.

Two CLI-visible operations:
- `designdoc resolve --emit-questions` prints JSON for the plugin to consume.
- `designdoc resolve --apply-fix HIL-XXX --fix "<text>"` patches the affected
  doc (replacing the inline HIL comment and stub phrasing) and marks the
  YAML entry status: resolved.
"""

from __future__ import annotations

import json
import re
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML

from designdoc.io_utils import atomic_write

HIL_COMMENT_RE = re.compile(r"<!-- HIL: (HIL-\d+)[^>]*-->")


def load_hil_yaml(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "unresolved_count": 0, "issues": []}
    return YAML(typ="rt").load(path.read_text()) or {
        "version": 1,
        "unresolved_count": 0,
        "issues": [],
    }


def save_hil_yaml(path: Path, doc: dict) -> None:
    y = YAML()
    y.indent(mapping=2, sequence=4, offset=2)
    # Atomic .tmp-then-replace via io_utils. ruamel writes to a stream; capture
    # into a buffer first.
    buf = StringIO()
    y.dump(doc, buf)
    atomic_write(path, buf.getvalue())


def emit_questions(hil_yaml: Path, output_dir: Path) -> dict:
    """Return a JSON-ready dict describing the first open HIL issue (or 'none')."""
    doc = load_hil_yaml(hil_yaml)
    for issue in doc.get("issues", []):
        if issue.get("status") == "open":
            artifact_path = output_dir / issue["artifact"]
            return {
                "id": issue["id"],
                "artifact": issue["artifact"],
                "artifact_exists": artifact_path.exists(),
                "stage": issue["stage"],
                "severity": issue["severity"],
                "doer_said": issue.get("doer_said", "")[:500],
                "checker_said": issue.get("checker_said", "")[:500],
                "attempts": issue.get("attempts", 3),
                "suggested_fixes": issue.get("suggested_fixes", [])[:4],
                "source_file": issue.get("source_file"),
            }
    return {"status": "none-open", "unresolved_count": 0}


def apply_fix(
    hil_yaml: Path,
    output_dir: Path,
    hil_id: str,
    fix_text: str,
) -> dict:
    """Patch the artifact: remove the inline HIL comment and replace with fix_text.

    Returns a summary dict: {applied: bool, artifact: path, reason?: str}.
    Mutates hil-issues.yaml to mark the issue resolved.
    """
    doc = load_hil_yaml(hil_yaml)
    issue = _find_issue(doc, hil_id)
    if issue is None:
        return {"applied": False, "reason": f"HIL id {hil_id} not found"}

    artifact = output_dir / issue["artifact"]
    if not artifact.exists():
        return {"applied": False, "reason": f"artifact missing: {artifact}"}

    body = artifact.read_text()
    new_body = _replace_hil_region(body, hil_id, fix_text)
    artifact.write_text(new_body)

    issue["status"] = "resolved"
    doc["unresolved_count"] = sum(1 for i in doc["issues"] if i.get("status") == "open")
    save_hil_yaml(hil_yaml, doc)
    return {"applied": True, "artifact": str(artifact.relative_to(output_dir))}


def _find_issue(doc: dict, hil_id: str) -> dict | None:
    return next((i for i in doc.get("issues", []) if i.get("id") == hil_id), None)


def _replace_hil_region(body: str, hil_id: str, fix_text: str) -> str:
    """Replace the `<!-- HIL: HIL-XXX ... -->` line with fix_text.

    If the comment isn't found (already resolved or manually removed), append
    the fix as a new paragraph at the end so the user's content isn't lost.
    """
    pattern = re.compile(rf"<!-- HIL: {re.escape(hil_id)}[^>]*-->\s*\n?", re.MULTILINE)
    if pattern.search(body):
        return pattern.sub(fix_text.rstrip() + "\n", body)
    return body.rstrip() + "\n\n" + fix_text.rstrip() + "\n"


def to_json(data: dict) -> str:
    return json.dumps(data, indent=2, default=str)
