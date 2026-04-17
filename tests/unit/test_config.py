"""Tests for the TOML config loader.

Critical invariant: max_attempts must NOT appear anywhere in the config model.
That rule is enforced by test_config_does_not_expose_max_attempts and is the
constitutional guard against users overriding the 3-attempt cap.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from designdoc.config import Config, load_config


def test_defaults_when_no_file(tmp_path: Path):
    cfg = load_config(None)
    assert cfg.max_budget_usd == 5.00
    assert cfg.parallelism == 3
    assert cfg.resume is True
    assert "node_modules" in cfg.exclude_paths
    assert cfg.output_dir == "docs/design"


def test_load_from_toml(tmp_path: Path):
    p = tmp_path / ".designdoc.toml"
    p.write_text("""
[pipeline]
max_budget_usd = 2.50
parallelism = 5
resume = false

[languages]
include = ["python"]
exclude_paths = [".venv", "build"]

[output]
dir = "custom/design"

[models]
doer = "claude-haiku-4-5-20251001"
checker = "claude-haiku-4-5-20251001"
""")
    cfg = load_config(p)
    assert cfg.max_budget_usd == 2.50
    assert cfg.parallelism == 5
    assert cfg.resume is False
    assert cfg.include_languages == ["python"]
    assert cfg.exclude_paths == [".venv", "build"]
    assert cfg.output_dir == "custom/design"
    assert cfg.doer_model == "claude-haiku-4-5-20251001"
    assert cfg.checker_model == "claude-haiku-4-5-20251001"


def test_missing_file_explicit_path_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_config_does_not_expose_max_attempts():
    """Constitutional guard: max_attempts MUST NOT be a Config field.

    If this test ever fails, someone added a field that could let users
    override the 3-attempt cap. That breaks the reliability invariant.
    """
    fields = Config.model_fields.keys()
    assert "max_attempts" not in fields
    assert "retries" not in fields
    assert "retry_count" not in fields
