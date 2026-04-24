"""The mermaid two-checker loop.

Wraps the generic doer_checker_loop with a composite checker: mmdc syntax
check FIRST (deterministic), then LLM semantic check if syntax passes. If
either fails, the whole attempt fails — ordering is enforced at the loop
level, not in a prompt.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from designdoc.agents.mermaid_generator import (
    build_doer_prompt,
    build_validator_prompt,
    make_mermaid_generator,
    make_mermaid_validator,
)
from designdoc.loop import ArtifactResult, doer_checker_loop
from designdoc.mermaid.mmdc import validate as mmdc_validate
from designdoc.runner import AgentDef, RunnerProtocol, RunResult
from designdoc.state import PipelineState

FENCE_RE = re.compile(r"^```(?:mermaid)?\s*\n?|\n?```\s*$", re.MULTILINE)


def strip_fence(text: str) -> str:
    """Strip leading/trailing ```mermaid fences the doer may wrap around its output."""
    return FENCE_RE.sub("", text).strip()


@dataclass
class _CompositeCheckerRunner:
    """Proxy runner that routes the synthetic ``mermaid-combined-checker`` agent
    through ``combined_check`` (mmdc syntax + LLM semantic). All other agents
    pass through to ``inner`` unchanged.

    Conforms to ``RunnerProtocol``: the loop only ever calls ``run(agent, prompt)``.
    Promoted to module level (rather than nested inside the factory) so it can
    be unit-tested in isolation.
    """

    inner: RunnerProtocol
    combined_check: Callable[[str], Awaitable[str]]

    async def run(self, agent: AgentDef, prompt: str) -> RunResult:
        if agent.name == "mermaid-combined-checker":
            text = await self.combined_check(prompt)
            return RunResult(text=text, input_tokens=0, output_tokens=0, cost_usd=0.0)
        return await self.inner.run(agent, prompt)


async def generate_validated_mermaid(
    *,
    artifact_name: str,
    artifact_text: str,
    runner: RunnerProtocol,
    hil_sink: list[dict],
    doer: AgentDef | None = None,
    validator: AgentDef | None = None,
    stage_name: str = "mermaid",
    state: PipelineState | None = None,
) -> ArtifactResult:
    """Generate a mermaid diagram for `artifact_text` and validate it.

    Uses doer_checker_loop with a composite checker function that the
    runner will invoke via the special "mermaid-combined-checker" agent
    whose output the loop parses as a CheckerVerdict.
    """
    doer = doer or make_mermaid_generator()
    validator = validator or make_mermaid_validator()

    async def combined_check(mermaid_text_fenced: str) -> str:
        """Returns a JSON verdict string (what the outer loop expects)."""
        mermaid_src = strip_fence(mermaid_text_fenced)

        syntax = mmdc_validate(mermaid_src)
        if not syntax.ok:
            return json.dumps(
                {
                    "status": "fail",
                    "summary": "mmdc syntax check failed",
                    "issues": [
                        {
                            "severity": "critical",
                            "location": "<mermaid source>",
                            "current_text": mermaid_src[:200],
                            "suggested_fix": f"fix syntax error: {syntax.stderr[:200]}",
                            "category": "syntax",
                        }
                    ],
                }
            )

        # Syntax passed — hand to semantic checker
        semantic_prompt = build_validator_prompt(artifact_name, artifact_text, mermaid_src)
        semantic_result = await runner.run(validator, semantic_prompt)
        return semantic_result.text

    # The combined checker is synthetic — it routes through combined_check.
    # The loop sees one "checker" AgentDef; our proxy intercepts and runs both.
    composite_checker = AgentDef(
        name="mermaid-combined-checker",
        system_prompt="(internal composite — syntax then semantic)",
        model="internal",
    )

    def checker_prompt_fn(mermaid_text: str) -> str:
        # The proxy doesn't care about the prompt text — it reads the doer's raw output.
        return mermaid_text

    return await doer_checker_loop(
        artifact_id=f"mermaid:{artifact_name}",
        doer=doer,
        checker=composite_checker,
        doer_prompt=build_doer_prompt(artifact_name, artifact_text),
        checker_prompt_fn=checker_prompt_fn,
        runner=_CompositeCheckerRunner(inner=runner, combined_check=combined_check),
        hil_sink=hil_sink,
        state=state,
        stage_name=stage_name,
    )
