"""The doer/checker bouncer — Gen 3 rule 1 (Python enforces control flow).

THIS FILE IS LOAD-BEARING. The three-attempt cap is a module-level constant
and is NEVER exposed to config. If someone asks to make it configurable, the
answer is no — the reliability claim depends on it being fixed.

Each call:
  1. Run doer with original prompt.
  2. Run checker on doer's output (isolated context — separate AgentDef).
  3. If pass, return.
  4. If fail and attempts left, build retry prompt from THIS attempt's issues
     only (not cumulative) and go back to step 1.
  5. If fail and at cap, append to hil_sink and ship the doc anyway.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from designdoc.runner import AgentDef, RunnerProtocol
from designdoc.verdict import (
    SYNTH_FAIL_PREFIX,
    CheckerIssue,
    CheckerVerdict,
    parse_verdict,
)

MAX_ATTEMPTS: int = 3
"""CANONICAL. Enforced here, nowhere else. Do not expose in config."""

DEBUG_DIR_ENV_VAR = "DESIGNDOC_DEBUG_DIR"
"""Env var that enables INV-001 raw-checker-output capture without any stage
code changes. Explicit debug_dir parameter wins over the env var."""


def _resolve_debug_dir(explicit: Path | None) -> Path | None:
    """Explicit parameter wins; otherwise fall back to env var; otherwise None."""
    if explicit is not None:
        return explicit
    env = os.environ.get(DEBUG_DIR_ENV_VAR)
    return Path(env) if env else None


ArtifactStatus = Literal["pass", "shipped_with_hil"]


@dataclass
class ArtifactResult:
    artifact_id: str
    status: ArtifactStatus
    text: str
    attempt: int
    verdict: CheckerVerdict


async def doer_checker_loop(
    *,
    artifact_id: str,
    doer: AgentDef,
    checker: AgentDef,
    doer_prompt: str,
    checker_prompt_fn: Callable[[str], str],
    runner: RunnerProtocol,
    hil_sink: list[dict],
    stage_name: str = "unknown",
    debug_dir: Path | None = None,
) -> ArtifactResult:
    """Run the doer/checker bouncer. Ships with HIL after MAX_ATTEMPTS failures.

    When debug_dir is set, every checker invocation's raw output + parse status
    is persisted under debug_dir. Opt-in diagnostic for INV-001.
    """
    effective_debug_dir = _resolve_debug_dir(debug_dir)
    current_text = (await runner.run(doer, doer_prompt)).text

    for attempt in range(1, MAX_ATTEMPTS + 1):
        checker_raw = (await runner.run(checker, checker_prompt_fn(current_text))).text
        verdict = parse_verdict(checker_raw, attempt=attempt, artifact_id=artifact_id)

        if effective_debug_dir is not None:
            _capture_checker_output(
                effective_debug_dir, artifact_id, stage_name, attempt, checker_raw, verdict
            )

        if verdict.status == "pass":
            return ArtifactResult(artifact_id, "pass", current_text, attempt, verdict)

        if attempt == MAX_ATTEMPTS:
            return _ship_with_hil(
                artifact_id=artifact_id,
                stage_name=stage_name,
                current_text=current_text,
                verdict=verdict,
                attempt=attempt,
                hil_sink=hil_sink,
            )

        # Retry with ONLY this attempt's issues — no cumulative drift.
        retry_prompt = _build_retry_prompt(doer_prompt, current_text, verdict)
        current_text = (await runner.run(doer, retry_prompt)).text

    raise AssertionError("unreachable")  # pragma: no cover


def _ship_with_hil(
    *,
    artifact_id: str,
    stage_name: str,
    current_text: str,
    verdict: CheckerVerdict,
    attempt: int,
    hil_sink: list[dict],
) -> ArtifactResult:
    """Append HIL entry and return shipped_with_hil ArtifactResult.

    Shared by doer_checker_loop and doer_schema_loop — the MAX_ATTEMPTS
    branch is identical in both and must stay identical (Invariant 5).
    """
    hil_sink.append(
        _build_hil_entry(artifact_id, stage_name, current_text, verdict, attempt, hil_sink)
    )
    return ArtifactResult(
        artifact_id,
        "shipped_with_hil",
        current_text,
        attempt,
        verdict,
    )


def _build_retry_prompt(original: str, previous_output: str, verdict: CheckerVerdict) -> str:
    """Construct the retry prompt. Framing: fix these specific issues only.

    Including the previous output is deliberate — the doer needs to see what
    got rejected so it doesn't reproduce the same mistake. Including the
    original task keeps context intact when the doer's session is fresh.
    """
    issues_block = "\n".join(
        f"- [{i.severity}] {i.location}: {i.suggested_fix}" for i in verdict.issues
    )
    return (
        "Your previous output was rejected by a reviewer. "
        "Address ONLY these specific issues:\n\n"
        f"{issues_block}\n\n"
        f"Original task:\n{original}\n\n"
        f"Previous output (for reference, do not repeat verbatim):\n{previous_output}"
    )


def _build_hil_entry(
    artifact_id: str,
    stage: str,
    final_text: str,
    verdict: CheckerVerdict,
    attempts: int,
    existing_sink: list[dict],
) -> dict:
    return {
        "id": f"HIL-{len(existing_sink) + 1:03d}",
        "artifact": artifact_id,
        "stage": stage,
        "severity": _max_severity(verdict),
        "doer_said": final_text[:500],
        "checker_said": verdict.summary or _first_issue_text(verdict),
        "attempts": attempts,
        "suggested_fixes": [i.suggested_fix for i in verdict.issues[:3]],
        "status": "open",
    }


def _capture_checker_output(
    debug_dir: Path,
    artifact_id: str,
    stage: str,
    attempt: int,
    raw: str,
    verdict: CheckerVerdict,
) -> None:
    """Persist raw checker output + parse result to debug_dir. INV-001 diagnostic.

    I/O failures are logged to stderr but do not halt the pipeline — diagnostic
    instrumentation must not be load-bearing for correctness.
    """
    parse_exc: str | None = None
    if verdict.status == "fail" and verdict.summary.startswith(SYNTH_FAIL_PREFIX):
        parse_exc = verdict.summary[len(SYNTH_FAIL_PREFIX) :].strip() or None

    payload = {
        "artifact_id": artifact_id,
        "stage": stage,
        "attempt": attempt,
        "timestamp": time.time(),
        "raw_output": raw,
        "raw_output_length": len(raw),
        "parse_status": verdict.status,
        "parse_exception": parse_exc,
        "parse_summary": verdict.summary,
    }

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", artifact_id)
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"{safe_name}__attempt{attempt}.json").write_text(
            json.dumps(payload, indent=2)
        )
    except OSError as e:
        print(f"[debug-capture] failed to write {safe_name}: {e}", file=sys.stderr)


def _max_severity(v: CheckerVerdict) -> str:
    order = {"critical": 3, "major": 2, "minor": 1}
    if not v.issues:
        return "minor"
    return max(v.issues, key=lambda i: order[i.severity]).severity


def _first_issue_text(v: CheckerVerdict) -> str:
    return v.issues[0].suggested_fix if v.issues else ""


async def doer_schema_loop(
    *,
    artifact_id: str,
    doer: AgentDef,
    doer_prompt: str,
    schema_model: type[BaseModel],
    runner: RunnerProtocol,
    hil_sink: list[dict],
    stage_name: str = "unknown",
) -> ArtifactResult:
    """Like doer_checker_loop but the checker is a pydantic schema.

    Use when the doer's output is strictly-structured and a parser suffices —
    saves the cost of a second LLM call. The retry prompt includes the exact
    pydantic ValidationError so the doer can self-correct the JSON shape.
    """
    current_text = (await runner.run(doer, doer_prompt)).text

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            schema_model.model_validate_json(current_text)
            verdict = CheckerVerdict(
                status="pass", attempt=attempt, artifact_id=artifact_id, summary="schema ok"
            )
            return ArtifactResult(artifact_id, "pass", current_text, attempt, verdict)
        except ValidationError as e:
            issues = [
                CheckerIssue(
                    severity="major",
                    location=".".join(str(p) for p in err["loc"]) or "<root>",
                    current_text=str(err.get("input", ""))[:120],
                    suggested_fix=err.get("msg", "fix this field"),
                )
                for err in e.errors()
            ] or [
                CheckerIssue(
                    severity="major",
                    location="<root>",
                    current_text=current_text[:120],
                    suggested_fix="return valid JSON matching the required schema",
                )
            ]
            verdict = CheckerVerdict(
                status="fail",
                attempt=attempt,
                artifact_id=artifact_id,
                summary=f"schema validation failed: {type(e).__name__}",
                issues=issues,
            )

        if attempt == MAX_ATTEMPTS:
            return _ship_with_hil(
                artifact_id=artifact_id,
                stage_name=stage_name,
                current_text=current_text,
                verdict=verdict,
                attempt=attempt,
                hil_sink=hil_sink,
            )

        retry_prompt = _build_retry_prompt(doer_prompt, current_text, verdict)
        current_text = (await runner.run(doer, retry_prompt)).text

    raise AssertionError("unreachable")  # pragma: no cover
