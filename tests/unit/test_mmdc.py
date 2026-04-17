"""Tests for the mmdc wrapper.

Gated by @pytest.mark.requires_mmdc so the test suite remains runnable on
machines without Node.js. The CI gate installs Node and runs these.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from designdoc.mermaid.mmdc import preflight, validate


def _mmdc_available() -> bool:
    """Return True if npx can resolve mmdc within a few seconds."""
    if shutil.which("npx") is None:
        return False
    try:
        r = subprocess.run(
            ["npx", "--yes", "@mermaid-js/mermaid-cli", "--version"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


pytestmark = pytest.mark.skipif(
    not _mmdc_available(),
    reason="requires @mermaid-js/mermaid-cli via npx (slow on first run)",
)


def test_preflight_passes_when_available():
    preflight()  # must not raise


def test_validate_accepts_valid_flowchart():
    src = "flowchart TD\n    A --> B\n    B --> C\n"
    result = validate(src)
    assert result.ok, f"expected ok=True, got stderr: {result.stderr}"


def test_validate_rejects_malformed_syntax():
    src = "flowchart TD\n    A -->> B ^^ nonsense\n"
    result = validate(src)
    assert not result.ok
    assert result.stderr  # must surface SOMETHING diagnostic


def test_validate_handles_empty_input():
    result = validate("")
    # Either rejected cleanly, or mmdc returns some stderr — we just need no crash
    assert isinstance(result.ok, bool)
