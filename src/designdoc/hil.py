"""Human-in-the-loop issue model and YAML emitter.

When a doer/checker pair exhausts 3 attempts, the artifact ships with an inline
`<!-- HIL: ID -->` comment and a structured entry appends to hil-issues.yaml.
The user resolves these later via `/designdoc resolve`.

ruamel.yaml is used instead of pyyaml so repeated appends preserve comments and
formatting — this matters because users edit the YAML between runs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Literal

from ruamel.yaml import YAML

from designdoc.io_utils import atomic_write

HILStatus = Literal["open", "resolved"]


@dataclass
class HILIssue:
    id: str
    artifact: str
    stage: str
    severity: Literal["critical", "major", "minor"]
    doer_said: str
    checker_said: str
    attempts: int
    status: HILStatus
    source_file: str | None = None
    category: str | None = None
    suggested_fixes: list[str] = field(default_factory=list)


def inline_comment(hil_id: str, note: str) -> str:
    """Build the HTML comment that marks a HIL dispute inline in a generated doc.

    Example: <!-- HIL: HIL-042 — retry policy, see hil-issues.yaml -->
    """
    return f"<!-- HIL: {hil_id} \u2014 {note.strip()}, see hil-issues.yaml -->"


def _yaml() -> YAML:
    y = YAML()
    y.indent(mapping=2, sequence=4, offset=2)
    y.preserve_quotes = True
    return y


def _load_or_init(path: Path) -> dict:
    if path.exists():
        return _yaml().load(path.read_text()) or _fresh_doc()
    return _fresh_doc()


def _fresh_doc() -> dict:
    return {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "unresolved_count": 0,
        "issues": [],
    }


def append_issue(path: Path, issue: HILIssue) -> None:
    """Append one HIL issue to the YAML file, creating it if missing.

    Recomputes unresolved_count from the full issue list (status=="open" only).
    Updates generated_at to the current time.
    """
    doc = _load_or_init(path)
    doc["issues"].append(asdict(issue))
    doc["unresolved_count"] = sum(1 for i in doc["issues"] if i["status"] == "open")
    doc["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    # Atomic .tmp-then-replace via io_utils, matching the rest of the project.
    # ruamel writes to a stream, so capture into a buffer then hand the string
    # to atomic_write.
    buf = StringIO()
    _yaml().dump(doc, buf)
    atomic_write(path, buf.getvalue())
