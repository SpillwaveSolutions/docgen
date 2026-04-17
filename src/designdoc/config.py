"""Pipeline configuration loaded from .designdoc.toml.

CONSTITUTIONAL GUARD: max_attempts is NOT a field here. It is fixed at 3 in
loop.py and must never be exposed as config. If you're tempted to add it,
re-read CLAUDE.md §3.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class Config(BaseModel):
    max_budget_usd: float = 5.00
    parallelism: int = 3
    resume: bool = True

    skip_stages: list[str] = Field(default_factory=list)
    only_stages: list[str] = Field(default_factory=list)

    include_languages: list[str] = Field(
        default_factory=lambda: ["python", "typescript", "javascript", "java", "go", "rust"]
    )
    exclude_paths: list[str] = Field(
        default_factory=lambda: [
            "node_modules", ".venv", "venv", "dist", "build", "target", ".git"
        ]
    )

    perplexity_mcp: bool = True
    context7_mcp: bool = True
    agent_brain_mcp: bool = False  # v1.1

    output_dir: str = "docs/design"
    diagram_format: str = "mermaid"

    doer_model: str = "claude-sonnet-4-6"
    checker_model: str = "claude-sonnet-4-6"


def load_config(path: Path | None) -> Config:
    """Load config from TOML. Returns defaults if path is None."""
    if path is None:
        return Config()
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    raw = tomllib.loads(path.read_text())
    pipe = raw.get("pipeline", {})
    stages = raw.get("stages", {})
    langs = raw.get("languages", {})
    mcp = raw.get("mcp", {})
    out = raw.get("output", {})
    models = raw.get("models", {})

    return Config(
        max_budget_usd=pipe.get("max_budget_usd", 5.00),
        parallelism=pipe.get("parallelism", 3),
        resume=pipe.get("resume", True),
        skip_stages=stages.get("skip", []),
        only_stages=stages.get("only", []),
        include_languages=langs.get("include", Config().include_languages),
        exclude_paths=langs.get("exclude_paths", Config().exclude_paths),
        perplexity_mcp=mcp.get("perplexity", True),
        context7_mcp=mcp.get("context7", True),
        agent_brain_mcp=mcp.get("agent_brain", False),
        output_dir=out.get("dir", "docs/design"),
        diagram_format=out.get("diagram_format", "mermaid"),
        doer_model=models.get("doer", "claude-sonnet-4-6"),
        checker_model=models.get("checker", "claude-sonnet-4-6"),
    )
