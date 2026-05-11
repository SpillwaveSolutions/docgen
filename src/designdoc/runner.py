"""ClaudeSDKRunner — the ONE place we call claude_agent_sdk.query().

Centralizes:
- Cost accrual (via CostAccumulator)
- Option normalization (AgentDef -> ClaudeAgentOptions)
- Stream collection (messages -> single RunResult)

Agents never call claude_agent_sdk directly; they always go through here.
Tests inject a fake `sdk` so unit tests run offline.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol

from designdoc.budget import CostAccumulator, UsageRecord

log = logging.getLogger(__name__)

# Issue #54: the bundled claude-agent-sdk CLI subprocess occasionally exits 1
# with a generic "Command failed with exit code" error. Treat that class of
# failure as retryable transport noise, not as a real run failure. The cap is
# bounded (not user-configurable) for the same reason as MAX_ATTEMPTS in
# loop.py: it's a correctness knob, not a tuning knob.
MAX_TRANSPORT_RETRIES = 3


def _is_transport_error(exc: BaseException) -> bool:
    """Return True for retryable transport-level errors from claude_agent_sdk.

    Covers both `claude_agent_sdk.ProcessError` (raised by the subprocess
    transport at exit) and the plain `Exception` raised by the SDK's message
    receiver when an "error"-type protocol message is encountered. We match
    on class name + message substring so this module stays importable in unit
    tests without a real `claude_agent_sdk` install.
    """
    if exc.__class__.__name__ == "ProcessError":
        return True
    return "Command failed with exit code" in str(exc)


@dataclass
class AgentDef:
    name: str
    system_prompt: str
    model: str
    allowed_tools: list[str] = field(default_factory=list)
    max_output_tokens: int = 4096
    mcp_servers: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class SDKProtocol(Protocol):
    async def query(self, *, prompt: str, options: dict) -> dict: ...


class RunnerProtocol(Protocol):
    """Public surface every doer/checker loop expects from a runner.

    `ClaudeSDKRunner` implements this; tests inject fakes that conform.
    Loop modules type-hint against this Protocol rather than `Any` so the
    runner contract is checkable.
    """

    async def run(self, agent: AgentDef, prompt: str) -> RunResult: ...


class ClaudeSDKRunner:
    def __init__(
        self,
        budget: CostAccumulator,
        sdk: SDKProtocol | None = None,
        cwd: str | None = None,
        transport_retry_backoff: float = 1.0,
    ):
        """cwd: working directory to expose to the SDK subprocess. When set,
        every options dict carries it through to ClaudeAgentOptions.cwd, so
        the SDK's Read/Grep tools resolve relative paths against the **target
        repo** rather than wherever the user invoked the CLI from. Issue #46.

        transport_retry_backoff: base seconds for exponential backoff between
        transport-error retries (1.0 → 1s, 2s, 4s). Issue #54. Tests pass 0.0
        to keep the suite fast; real callers leave the default.
        """
        self.budget = budget
        self.sdk = sdk if sdk is not None else _DefaultSDK()
        self.cwd = cwd
        self.transport_retry_backoff = transport_retry_backoff

    async def run(self, agent: AgentDef, prompt: str) -> RunResult:
        options = _build_options(agent, cwd=self.cwd)
        resp = await self._query_with_retry(agent.name, prompt, options)
        usage = resp.get("usage", {}) or {}
        rec = UsageRecord(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cost_usd=usage.get("cost_usd", 0.0),
            agent=agent.name,
        )
        self.budget.accrue(rec)  # raises BudgetExceededError before mutating state
        return RunResult(
            text=resp.get("text", ""),
            input_tokens=rec.input_tokens,
            output_tokens=rec.output_tokens,
            cost_usd=rec.cost_usd,
        )

    async def _query_with_retry(self, agent_name: str, prompt: str, options: dict) -> dict:
        """Call self.sdk.query with bounded retry on transport-level errors.

        Issue #54: the bundled claude CLI subprocess occasionally exits 1 mid
        stream; the SDK surfaces this as an Exception whose message starts with
        "Command failed with exit code". A single flake should not abort the
        whole stage's TaskGroup — retry up to MAX_TRANSPORT_RETRIES times with
        exponential backoff. Non-transport exceptions propagate immediately
        so real bugs aren't masked by retry-and-delay.
        """
        last_exc: BaseException | None = None
        for attempt in range(MAX_TRANSPORT_RETRIES + 1):
            try:
                return await self.sdk.query(prompt=prompt, options=options)
            except Exception as exc:
                if not _is_transport_error(exc):
                    raise
                last_exc = exc
                if attempt == MAX_TRANSPORT_RETRIES:
                    log.warning(
                        "agent %s: transport error after %d attempts, giving up: %s",
                        agent_name,
                        attempt + 1,
                        exc,
                    )
                    raise
                sleep_s = self.transport_retry_backoff * (2**attempt)
                log.warning(
                    "agent %s: transport error on attempt %d (%s); retrying in %.1fs",
                    agent_name,
                    attempt + 1,
                    exc,
                    sleep_s,
                )
                if sleep_s > 0:
                    await asyncio.sleep(sleep_s)
        # Unreachable: the loop above either returns or raises.
        raise last_exc  # type: ignore[misc]


def _build_options(agent: AgentDef, cwd: str | None = None) -> dict:
    """Translate AgentDef into the options dict passed to the SDK.

    When agent.mcp_servers is non-empty:
    - Adds `mcp__<server>__*` entries to allowed_tools so the agent can
      actually invoke each server's tools (the SDK doesn't auto-allow).
    - Adds setting_sources=["user", "project", "local"] so MCP server
      configs declared in ~/.claude.json, .mcp.json, or project local
      flow through without us having to hand-wire each server's transport.

    When cwd is provided (issue #46), it is included in the options dict
    so the SDK subprocess uses it as its working directory. Without this,
    the SDK's Read/Grep tools resolve paths relative to wherever the user
    invoked the CLI from — and class_documenter cannot read source files
    in the target repo when target_repo != cli_invocation_cwd.
    """
    tools = list(agent.allowed_tools)
    extras: dict = {}
    if agent.mcp_servers:
        for server in agent.mcp_servers:
            tools.append(f"mcp__{server}__*")
        extras["setting_sources"] = ["user", "project", "local"]
    else:
        # Issue #49: hermetic non-MCP agents. Without this, the SDK's default
        # (setting_sources=None → load user+project+local) pulls in
        # ~/.claude/CLAUDE.md, the target repo's CLAUDE.md, and the user's
        # active output style — polluting class docs with `★ Insight ───` blocks
        # and absorbing target-repo conventions into the doer's reasoning.
        # SDK contract: setting_sources=[] means "isolation mode."
        extras["setting_sources"] = []
    if cwd is not None:
        extras["cwd"] = cwd

    return {
        "system_prompt": agent.system_prompt,
        "model": agent.model,
        "allowed_tools": tools,
        "mcp_servers": list(agent.mcp_servers),
        **extras,
    }


class _DefaultSDK:
    """Thin adapter over claude_agent_sdk.query(). Imported lazily so unit tests
    that use a FakeSDK don't require the real package to be importable at module load."""

    async def query(self, *, prompt: str, options: dict) -> dict:
        from claude_agent_sdk import (  # noqa: PLC0415 — lazy on purpose
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        extra: dict = {}
        if "setting_sources" in options:
            # Note: explicit `[]` (isolation mode for issue #49) is falsy.
            # `if options.get(...)` would silently drop it; `in` is correct.
            extra["setting_sources"] = options["setting_sources"]
        if options.get("cwd"):
            # Issue #46: pin the SDK subprocess to the target repo so its
            # Read/Grep tools see the source files we're documenting.
            extra["cwd"] = options["cwd"]
        # MCP server dict is built from inherited settings — we don't pass
        # explicit configs here; agent names flow via allowed_tools patterns.
        opts = ClaudeAgentOptions(
            system_prompt=options.get("system_prompt"),
            model=options.get("model"),
            allowed_tools=options.get("allowed_tools") or [],
            **extra,
        )

        text_parts: list[str] = []
        total_cost = 0.0
        input_tokens = 0
        output_tokens = 0

        async for msg in query(prompt=prompt, options=opts):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
            elif isinstance(msg, ResultMessage):
                total_cost = msg.total_cost_usd or 0.0
                usage = msg.usage or {}
                input_tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
                output_tokens = usage.get("output_tokens", 0) if isinstance(usage, dict) else 0

        return {
            "text": "".join(text_parts),
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": total_cost,
            },
        }
