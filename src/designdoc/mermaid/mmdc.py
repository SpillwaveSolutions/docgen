"""Shell out to `@mermaid-js/mermaid-cli` via npx for mermaid syntax validation.

Preflight probe runs once at orchestrator start — if `npx --yes mmdc --version`
fails, the pipeline halts before burning budget on Stage 5.

`validate(text)` parses a single mermaid block and returns (ok, stderr). We
render to a throwaway SVG in tmp because mmdc has no proper `--dry-run` flag
in older releases; a successful render implies a parseable source.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class MmdcNotAvailableError(RuntimeError):
    """Raised when npx is missing or `npx --yes mmdc --version` fails."""


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    stderr: str = ""


def preflight() -> None:
    """Raise MmdcNotAvailableError if mmdc can't be invoked.

    This must be called once at orchestrator start so Stage 5 doesn't spend
    LLM budget on diagrams we can't validate.
    """
    if shutil.which("npx") is None:
        raise MmdcNotAvailableError("npx is not on PATH; install Node.js to use the mermaid stage")
    try:
        r = subprocess.run(
            ["npx", "--yes", "@mermaid-js/mermaid-cli", "--version"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as e:
        raise MmdcNotAvailableError("`npx --yes mmdc --version` timed out") from e
    if r.returncode != 0:
        raise MmdcNotAvailableError(
            f"`npx --yes @mermaid-js/mermaid-cli --version` failed with exit "
            f"{r.returncode}: {r.stderr.strip() or r.stdout.strip()}"
        )


def validate(mermaid_text: str, *, timeout: float = 30.0) -> ValidationResult:
    """Parse a mermaid block. Returns ValidationResult(ok=bool, stderr=str).

    On success, ok=True and stderr is empty (or informational). On failure,
    ok=False and stderr contains the parser error.
    """
    if shutil.which("npx") is None:
        return ValidationResult(ok=False, stderr="npx not on PATH")

    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        src = tmp / "diagram.mmd"
        src.write_text(mermaid_text)
        out = tmp / "diagram.svg"
        try:
            r = subprocess.run(
                [
                    "npx",
                    "--yes",
                    "@mermaid-js/mermaid-cli",
                    "-q",
                    "-i",
                    str(src),
                    "-o",
                    str(out),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ValidationResult(ok=False, stderr=f"mmdc timed out after {timeout}s")
        if r.returncode != 0:
            return ValidationResult(ok=False, stderr=r.stderr.strip() or r.stdout.strip())
        # mmdc may print deprecation warnings to stderr on success — that's fine
        return ValidationResult(ok=True, stderr=r.stderr.strip())
