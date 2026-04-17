"""Smoke test: the package imports and the public surface is reachable.

This also serves as a guard against pytest-exit-code-5 when the integration
directory has no other tests yet — CI depends on at least one test running here.
"""

from __future__ import annotations


def test_package_imports_with_version():
    import designdoc

    assert designdoc.__version__


def test_public_surface_reachable():
    """Everything the orchestrator will wire up must be importable."""
    from designdoc.budget import CostAccumulator
    from designdoc.config import Config, load_config
    from designdoc.hil import HILIssue, append_issue, inline_comment
    from designdoc.loop import MAX_ATTEMPTS, doer_checker_loop
    from designdoc.runner import AgentDef, ClaudeSDKRunner, RunResult
    from designdoc.state import PipelineState, StageStatus
    from designdoc.verdict import CheckerIssue, CheckerVerdict, MermaidIssue, parse_verdict

    # MAX_ATTEMPTS is the canonical cap — if this changes, something is wrong
    assert MAX_ATTEMPTS == 3

    # Smoke-check constructors don't raise
    assert CostAccumulator(cap_usd=1.0)
    assert Config()
    assert PipelineState.load_or_new
    assert load_config
    assert doer_checker_loop
    assert ClaudeSDKRunner
    assert append_issue
    assert inline_comment
    assert parse_verdict
    _ = (AgentDef, RunResult, StageStatus, CheckerIssue, CheckerVerdict, MermaidIssue, HILIssue)
