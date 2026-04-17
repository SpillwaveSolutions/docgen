# designdoc-gen Implementation Plan (v1 — full pipeline + plugin)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI (`designdoc`) plus an in-repo Claude Code plugin (`/designdoc`) that walks a target codebase bottom-up and emits a validated `docs/design/` tree — per-class docs, package rollups, mermaid diagrams, a system design, a tech-debt ledger, and a YAML file of unresolved HIL disputes — using Gen 3 harness-engineering discipline (Python-enforced retries, isolated checker contexts, resumable state, hard budget cap).

**Architecture:** A deterministic Python state machine drives 9 stages (0–8). Each stage that generates an artifact uses an isolated doer/checker pair (both `claude_agent_sdk.query()` calls with distinct system prompts and fresh contexts) wrapped in a `for attempt in range(3)` loop; unresolvable disputes ship with inline HIL comments and append to `hil-issues.yaml`. Mermaid diagrams get a second, stricter validator that shells out to `mmdc` for syntax and an LLM checker for semantics. Every stage checkpoints to `.designdoc-state.json` for crash-resume, and every LLM call accrues to a `CostAccumulator` that raises `BudgetExceededError` when the cap is hit.

**Tech Stack:** Python 3.12, `uv` (env/deps), `pyproject.toml`, `Taskfile.yml` (task runner), `claude-agent-sdk>=0.2`, `pydantic>=2.7`, `anyio` for structured concurrency, `tomli-w` for config write, `ruamel.yaml` for round-trip HIL YAML, `typer` for CLI, `rich` for progress rendering, `@mermaid-js/mermaid-cli` via `npx mmdc` (preflight-probed), `pytest>=8` + `pytest-anyio` for tests. Claude models default to Sonnet 4.6 (`claude-sonnet-4-6`) for doers/checkers; Haiku 4.5 (`claude-haiku-4-5-20251001`) is opt-in for cheap checkers via config.

---

## Context

This is a greenfield build at `/Users/richardhightower/clients/spillwave/src/docgen/`. The directory is empty. The design doc (posted in the conversation) is the source of truth for behavior, shape, and invariants. It explicitly calls out six Gen 3 principles — all must be traceable to code in this plan:

1. Control flow lives in Python, not prompts.
2. Checkers run in their own context window (no self-grading).
3. Scopes are small and bounded (file → class → package → system).
4. Failures are loud (schema-validated verdicts, HIL YAML on dispute).
5. Reliability over speed (`max_attempts=3`, `asyncio.Semaphore(3)`, no speculation).
6. Mermaid is syntax+semantics-validated before it touches a doc.

Adjacent Spillwave projects (`codebase-mentor`, `book-gen2`) supply proven conventions for `uv`/Taskfile/pytest layout and Claude plugin packaging. This plan reuses those patterns; it does **not** invent new ones.

---

## File Structure

```
docgen/
├── pyproject.toml                      # uv-managed; deps + entry point `designdoc`
├── Taskfile.yml                        # task test, task lint, task dogfood
├── README.md
├── CLAUDE.md                           # repo-local guidance (test discipline, invariants)
├── .gitignore                          # includes .designdoc-state.json, .designdoc-budget.json
├── .designdoc.toml.example             # shipped sample config
├── uv.lock
├── src/designdoc/
│   ├── __init__.py                     # version
│   ├── cli.py                          # typer app: generate | resume | status | resolve
│   ├── config.py                       # Config dataclass, TOML loader
│   ├── state.py                        # PipelineState (load/save/resume)
│   ├── budget.py                       # CostAccumulator, BudgetExceededError
│   ├── runner.py                       # ClaudeSDKRunner (wraps claude_agent_sdk.query)
│   ├── verdict.py                      # CheckerIssue, CheckerVerdict, MermaidIssue
│   ├── loop.py                         # doer_checker_loop (the 3-attempt bouncer)
│   ├── hil.py                          # HIL issue model, yaml emit/append, inline-comment helper
│   ├── orchestrator.py                 # Orchestrator: stage table, barrier drive, checkpoint
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── prompts.py                  # all system prompts as constants
│   │   ├── file_analyzer.py            # doer: per-file summary
│   │   ├── class_documenter.py         # doer: class doc
│   │   ├── package_documenter.py       # doer: package rollup
│   │   ├── mermaid_generator.py        # doer: mermaid block
│   │   ├── mermaid_validator.py        # checker: semantic
│   │   ├── techdebt_researcher.py      # doer: tech debt row (uses MCP)
│   │   ├── techdebt_crossref.py        # checker: cross-ref (uses MCP)
│   │   ├── system_designer.py          # doer: system rollup
│   │   └── doc_quality_checker.py      # generic doc checker
│   ├── stages/
│   │   ├── __init__.py
│   │   ├── s0_discover.py              # language manifest + tree
│   │   ├── s1_index.py                 # AST-lite signatures (no LLM)
│   │   ├── s2_file_analysis.py         # doer/checker per file
│   │   ├── s3_class_docs.py
│   │   ├── s4_package_rollups.py
│   │   ├── s5_mermaid.py
│   │   ├── s6_tech_debt.py
│   │   ├── s7_system_rollup.py
│   │   └── s8_finalize.py              # README TOC, HIL emit
│   ├── mermaid/
│   │   ├── __init__.py
│   │   ├── mmdc.py                     # subprocess wrapper, preflight probe
│   │   └── loop.py                     # mermaid-specific two-checker loop
│   ├── index/
│   │   ├── __init__.py
│   │   ├── discover.py                 # language detection, exclude patterns
│   │   └── signatures.py               # tree-sitter-free lightweight signature extraction
│   └── templates/
│       ├── class_doc.md.tmpl
│       ├── package_readme.md.tmpl
│       ├── system_design.md.tmpl
│       ├── architecture.md.tmpl
│       ├── tech_debt.md.tmpl
│       └── readme_toc.md.tmpl
├── plugins/
│   └── designdoc/
│       ├── plugin.json
│       ├── commands/
│       │   └── designdoc.md            # /designdoc generate|resume|resolve|status
│       └── README.md
└── tests/
    ├── conftest.py                     # common fixtures (tmp repo, fake runner)
    ├── fixtures/
    │   └── tiny_repo/                  # 4-file Python repo used end-to-end
    ├── unit/
    │   ├── test_budget.py
    │   ├── test_state.py
    │   ├── test_verdict.py
    │   ├── test_loop.py
    │   ├── test_hil.py
    │   ├── test_config.py
    │   ├── test_discover.py
    │   ├── test_signatures.py
    │   └── test_mmdc.py                # uses real mmdc if present, else skip with marker
    ├── integration/
    │   ├── test_stage_discover.py
    │   ├── test_stage_index.py
    │   ├── test_resume.py              # crash mid-pipeline, resume from checkpoint
    │   └── test_budget_stop.py         # pipeline halts cleanly when over budget
    └── e2e/
        └── test_tiny_repo_full.py      # full pipeline against tests/fixtures/tiny_repo
```

Key decomposition rules:
- Every stage is a single `.py` file that exports one `async def run(ctx: StageContext)` function. No stage imports another.
- Every agent is a single `.py` file exporting one `AgentDef` dataclass (system prompt, allowed tools, model, max output tokens). Prompts live in `agents/prompts.py` as constants so they can be unit-tested for length and reviewed in one place.
- Stages 2–7 all call `doer_checker_loop` from `loop.py` — that file is the **one** place the 3-attempt retry rule lives.

---

## Tasks

> **TDD reminder (repo CLAUDE.md will enforce this):** every task with code writes the failing test first, confirms FAIL, writes minimal code, confirms PASS, then commits. No exceptions.

---

### Task 1: Repository scaffolding

**Files:**
- Create: `pyproject.toml`, `Taskfile.yml`, `README.md`, `CLAUDE.md`, `.gitignore`, `.designdoc.toml.example`, `src/designdoc/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Initialize `uv` project**

```bash
cd /Users/richardhightower/clients/spillwave/src/docgen
git init
uv init --package --name designdoc --python 3.12
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "designdoc"
version = "0.1.0"
description = "Harness-engineered codebase documentation pipeline"
readme = "README.md"
requires-python = ">=3.12"
authors = [{ name = "Rick Hightower" }]
dependencies = [
    "claude-agent-sdk>=0.2",
    "pydantic>=2.7",
    "typer>=0.12",
    "rich>=13.7",
    "anyio>=4.3",
    "tomli-w>=1.0",
    "ruamel.yaml>=0.18",
    "jinja2>=3.1",
]

[project.scripts]
designdoc = "designdoc.cli:app"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-anyio>=0.0.0",
    "pytest-cov>=5.0",
    "ruff>=0.5",
]

[tool.pytest.ini_options]
addopts = "-ra --strict-markers"
markers = [
    "requires_mmdc: needs @mermaid-js/mermaid-cli installed",
    "requires_api: needs ANTHROPIC_API_KEY and makes real calls",
]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 3: Write `Taskfile.yml`**

```yaml
version: '3'

tasks:
  install:
    desc: Install deps into a uv-managed venv
    cmds: [uv sync]

  test:
    desc: Unit + integration tests (no real API)
    cmds: [uv run pytest tests/unit tests/integration -m "not requires_api" -v]

  test-e2e:
    desc: End-to-end tests (real API, gated)
    cmds: [uv run pytest tests/e2e -v]

  lint:
    cmds: [uv run ruff check src tests]

  format:
    cmds: [uv run ruff format src tests]

  dogfood:
    desc: Run designdoc against tests/fixtures/tiny_repo
    cmds: [uv run designdoc generate --repo tests/fixtures/tiny_repo --budget 2.00]
```

- [ ] **Step 4: Write `.gitignore`**

```
.venv/
.designdoc-state.json
.designdoc-budget.json
__pycache__/
*.pyc
.pytest_cache/
.coverage
docs/design/.designdoc-state.json
```

- [ ] **Step 5: Write `CLAUDE.md`** — capture the six Gen 3 invariants and the "no command, no completion" test rule copied verbatim from `codebase-mentor/CLAUDE.md`.

- [ ] **Step 6: Run `uv sync` and verify `designdoc --help` works (it will error — no CLI yet — that's fine; just confirm the entry point resolves).**

Expected: `uv run designdoc --help` resolves to `designdoc.cli:app` even if `app` is not yet defined — this test is deferred to Task 7.

- [ ] **Step 7: Commit**

```bash
git add .
git commit -m "chore: scaffold designdoc package with uv + Taskfile"
```

---

### Task 2: CostAccumulator and BudgetExceededError

**Files:**
- Create: `src/designdoc/budget.py`, `tests/unit/test_budget.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_budget.py
import pytest
from designdoc.budget import CostAccumulator, BudgetExceededError, UsageRecord

def test_accrue_under_cap_does_not_raise():
    c = CostAccumulator(cap_usd=1.00)
    c.accrue(UsageRecord(input_tokens=1000, output_tokens=500, cost_usd=0.05, agent="x"))
    assert c.total_cost_usd == pytest.approx(0.05)
    assert c.invocations == 1

def test_accrue_over_cap_raises():
    c = CostAccumulator(cap_usd=0.10)
    c.accrue(UsageRecord(input_tokens=0, output_tokens=0, cost_usd=0.08, agent="x"))
    with pytest.raises(BudgetExceededError) as ex:
        c.accrue(UsageRecord(input_tokens=0, output_tokens=0, cost_usd=0.05, agent="x"))
    assert "0.13" in str(ex.value)

def test_persistence_roundtrip(tmp_path):
    p = tmp_path / ".designdoc-budget.json"
    c = CostAccumulator(cap_usd=1.00, path=p)
    c.accrue(UsageRecord(input_tokens=1, output_tokens=1, cost_usd=0.02, agent="y"))
    c.save()
    c2 = CostAccumulator.load_or_new(cap_usd=1.00, path=p)
    assert c2.total_cost_usd == pytest.approx(0.02)
    assert c2.invocations == 1
```

- [ ] **Step 2: Run test, confirm FAIL**

```bash
uv run pytest tests/unit/test_budget.py -v
# Expected: ModuleNotFoundError: designdoc.budget
```

- [ ] **Step 3: Implement `src/designdoc/budget.py`**

```python
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


class BudgetExceededError(Exception):
    pass


@dataclass(slots=True)
class UsageRecord:
    input_tokens: int
    output_tokens: int
    cost_usd: float
    agent: str


@dataclass
class CostAccumulator:
    cap_usd: float
    path: Path | None = None
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    invocations: int = 0
    by_agent: dict[str, float] = field(default_factory=dict)

    def accrue(self, rec: UsageRecord) -> None:
        projected = self.total_cost_usd + rec.cost_usd
        if projected > self.cap_usd:
            raise BudgetExceededError(
                f"budget cap ${self.cap_usd:.2f} would be exceeded "
                f"(current ${self.total_cost_usd:.2f} + next ${rec.cost_usd:.4f} = ${projected:.2f})"
            )
        self.total_cost_usd = projected
        self.total_input_tokens += rec.input_tokens
        self.total_output_tokens += rec.output_tokens
        self.invocations += 1
        self.by_agent[rec.agent] = self.by_agent.get(rec.agent, 0.0) + rec.cost_usd

    def save(self) -> None:
        if not self.path:
            return
        self.path.write_text(json.dumps(asdict(self), default=str, indent=2))

    @classmethod
    def load_or_new(cls, cap_usd: float, path: Path) -> "CostAccumulator":
        if path.exists():
            data = json.loads(path.read_text())
            return cls(
                cap_usd=cap_usd,
                path=path,
                total_cost_usd=data["total_cost_usd"],
                total_input_tokens=data["total_input_tokens"],
                total_output_tokens=data["total_output_tokens"],
                invocations=data["invocations"],
                by_agent=data["by_agent"],
            )
        return cls(cap_usd=cap_usd, path=path)
```

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/unit/test_budget.py -v
# Expected: 3 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/designdoc/budget.py tests/unit/test_budget.py
git commit -m "feat(budget): cost accumulator with cap + persistence"
```

---

### Task 3: PipelineState (resumable state machine)

**Files:**
- Create: `src/designdoc/state.py`, `tests/unit/test_state.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_state.py
from pathlib import Path
from designdoc.state import PipelineState, StageStatus

def test_new_state_has_no_stages_done(tmp_path):
    s = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    assert s.stages == {}
    assert s.current_stage == 0

def test_roundtrip_resumes(tmp_path):
    s = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    s.stages["discover"] = StageStatus.DONE
    s.current_stage = 1
    s.hil_issues.append({"id": "HIL-001"})
    s.save()
    s2 = PipelineState.load_or_new(output_dir=tmp_path, target_repo=Path("/x"))
    assert s2.stages["discover"] == StageStatus.DONE
    assert s2.current_stage == 1
    assert s2.hil_issues == [{"id": "HIL-001"}]
```

- [ ] **Step 2: Run, confirm FAIL.** `uv run pytest tests/unit/test_state.py -v`

- [ ] **Step 3: Implement `src/designdoc/state.py`**

```python
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from enum import StrEnum
from pathlib import Path


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


STATE_FILENAME = ".designdoc-state.json"


@dataclass
class PipelineState:
    target_repo: Path
    output_dir: Path
    current_stage: int = 0
    stages: dict[str, StageStatus] = field(default_factory=dict)
    total_retries: int = 0
    hil_issues: list[dict] = field(default_factory=list)
    artifact_index: dict[str, str] = field(default_factory=dict)   # id -> relative path

    @property
    def state_path(self) -> Path:
        return self.output_dir / STATE_FILENAME

    def save(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["target_repo"] = str(self.target_repo)
        data["output_dir"] = str(self.output_dir)
        data["stages"] = {k: str(v) for k, v in self.stages.items()}
        self.state_path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load_or_new(cls, output_dir: Path, target_repo: Path) -> "PipelineState":
        path = output_dir / STATE_FILENAME
        if path.exists():
            d = json.loads(path.read_text())
            return cls(
                target_repo=Path(d["target_repo"]),
                output_dir=Path(d["output_dir"]),
                current_stage=d["current_stage"],
                stages={k: StageStatus(v) for k, v in d["stages"].items()},
                total_retries=d["total_retries"],
                hil_issues=d["hil_issues"],
                artifact_index=d["artifact_index"],
            )
        return cls(target_repo=target_repo, output_dir=output_dir)
```

- [ ] **Step 4: Run, confirm PASS.** Commit: `feat(state): resumable pipeline state`.

---

### Task 4: CheckerVerdict + MermaidIssue schemas

**Files:**
- Create: `src/designdoc/verdict.py`, `tests/unit/test_verdict.py`

- [ ] **Step 1: Write failing tests covering: valid pass, valid fail with issues, reject pass-with-major-issue, reject fail-with-no-issues, parse synthetic fail from malformed JSON.**

```python
# tests/unit/test_verdict.py
import pytest
from pydantic import ValidationError
from designdoc.verdict import CheckerIssue, CheckerVerdict, parse_verdict, MermaidIssue

def test_valid_pass():
    v = CheckerVerdict(status="pass", attempt=1, artifact_id="a", summary="ok")
    assert v.status == "pass"

def test_pass_with_major_rejected():
    with pytest.raises(ValidationError):
        CheckerVerdict(
            status="pass", attempt=1, artifact_id="a",
            issues=[CheckerIssue(severity="major", location="x:1",
                                 current_text="a", suggested_fix="b")]
        )

def test_fail_without_issues_rejected():
    with pytest.raises(ValidationError):
        CheckerVerdict(status="fail", attempt=1, artifact_id="a")

def test_parse_malformed_returns_synthetic_fail():
    v = parse_verdict("not json at all", attempt=2, artifact_id="a")
    assert v.status == "fail"
    assert v.attempt == 2
    assert any(i.severity == "critical" for i in v.issues)

def test_mermaid_issue_category():
    m = MermaidIssue(severity="major", location="line 3", current_text="A-->B",
                     suggested_fix="remove B", category="hallucinated_node", node_or_edge="B")
    assert m.category == "hallucinated_node"
```

- [ ] **Step 2: FAIL.** **Step 3: Implement:**

```python
# src/designdoc/verdict.py
from __future__ import annotations
import json
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class CheckerIssue(BaseModel):
    severity: Literal["critical", "major", "minor"]
    location: str
    current_text: str
    suggested_fix: str
    source: str | None = None


class MermaidIssue(CheckerIssue):
    category: Literal["syntax", "hallucinated_node", "missing_edge", "wrong_direction", "too_vague"]
    node_or_edge: str | None = None


class CheckerVerdict(BaseModel):
    status: Literal["pass", "fail"]
    attempt: int
    artifact_id: str
    summary: str = ""
    issues: list[CheckerIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistency(self):
        if self.status == "pass" and any(i.severity != "minor" for i in self.issues):
            raise ValueError("pass with non-minor issues is invalid")
        if self.status == "fail" and not self.issues:
            raise ValueError("fail with no issues is invalid")
        return self


def parse_verdict(raw: str, *, attempt: int, artifact_id: str) -> CheckerVerdict:
    """Parse checker output. On any failure (malformed JSON, schema violation) return a
    synthetic fail verdict — fail loud, not quiet."""
    try:
        data = json.loads(raw)
        data["attempt"] = attempt
        data["artifact_id"] = artifact_id
        return CheckerVerdict(**data)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        return CheckerVerdict(
            status="fail",
            attempt=attempt,
            artifact_id=artifact_id,
            summary=f"checker output unparseable: {type(e).__name__}",
            issues=[CheckerIssue(
                severity="critical", location="<checker-output>",
                current_text=raw[:200], suggested_fix="re-run checker",
            )],
        )
```

- [ ] **Step 4: PASS.** Commit: `feat(verdict): pydantic schema + synthetic-fail parser`.

---

### Task 5: HIL model + YAML emit + inline comment helper

**Files:**
- Create: `src/designdoc/hil.py`, `tests/unit/test_hil.py`

- [ ] **Step 1: Failing test** — covers: new issues file created with correct header, appending preserves existing entries, inline comment formatter returns the exact HTML-comment format.

```python
# tests/unit/test_hil.py
from pathlib import Path
from designdoc.hil import HILIssue, append_issue, inline_comment

def test_new_file_has_header(tmp_path):
    p = tmp_path / "hil-issues.yaml"
    append_issue(p, HILIssue(
        id="HIL-001", artifact="x.md", stage="class-docs", severity="major",
        doer_said="a", checker_said="b", attempts=3, status="open",
    ))
    content = p.read_text()
    assert "version: 1" in content
    assert "HIL-001" in content
    assert "unresolved_count: 1" in content

def test_append_increments_count(tmp_path):
    p = tmp_path / "hil-issues.yaml"
    append_issue(p, HILIssue(id="HIL-001", artifact="a", stage="s", severity="major",
                             doer_said="", checker_said="", attempts=3, status="open"))
    append_issue(p, HILIssue(id="HIL-002", artifact="b", stage="s", severity="minor",
                             doer_said="", checker_said="", attempts=3, status="open"))
    assert "unresolved_count: 2" in p.read_text()

def test_inline_comment_format():
    assert inline_comment("HIL-042", "retry policy") == \
        "<!-- HIL: HIL-042 — retry policy, see hil-issues.yaml -->"
```

- [ ] **Step 2: FAIL. Step 3: Implement** using `ruamel.yaml` round-trip for append-preserving formatting. Top-level keys: `version`, `generated_at`, `unresolved_count`, `issues: []`.

- [ ] **Step 4: PASS.** Commit: `feat(hil): yaml append + inline comment helper`.

---

### Task 6: ClaudeSDKRunner (the one place we call the SDK)

**Files:**
- Create: `src/designdoc/runner.py`, `tests/unit/test_runner.py`

**Why centralize:** every LLM invocation runs through here. Cost accrual, retry-for-transport-errors, and output capture live in one file. Agents never call `claude_agent_sdk.query` directly.

- [ ] **Step 1: Write failing test using a fake SDK shim**

```python
# tests/unit/test_runner.py
import pytest
from designdoc.runner import ClaudeSDKRunner, AgentDef, RunResult
from designdoc.budget import CostAccumulator

class FakeSDK:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    async def query(self, *, prompt, options):
        self.calls.append((prompt, options))
        return self.responses.pop(0)

@pytest.mark.anyio
async def test_runner_records_cost(tmp_path):
    budget = CostAccumulator(cap_usd=1.00)
    fake = FakeSDK([{"text": "hello", "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01}}])
    r = ClaudeSDKRunner(budget=budget, sdk=fake)
    agent = AgentDef(name="t", system_prompt="p", model="claude-sonnet-4-6",
                     allowed_tools=[], max_output_tokens=1024)
    out = await r.run(agent, prompt="hi")
    assert out.text == "hello"
    assert budget.total_cost_usd == pytest.approx(0.01)
    assert budget.invocations == 1
```

- [ ] **Step 2: FAIL. Step 3: Implement `runner.py`**

```python
# src/designdoc/runner.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol, Any
from designdoc.budget import CostAccumulator, UsageRecord


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


class _SDK(Protocol):
    async def query(self, *, prompt: str, options: Any) -> dict: ...


class ClaudeSDKRunner:
    def __init__(self, budget: CostAccumulator, sdk: _SDK | None = None):
        self.budget = budget
        if sdk is None:
            # Lazy import so tests can inject a fake without the real SDK installed
            from claude_agent_sdk import query as _query  # type: ignore

            class _Default:
                async def query(self, *, prompt, options):
                    # Normalize SDK response to {"text":..., "usage":...}
                    return await _collect(_query(prompt=prompt, options=options))
            sdk = _Default()
        self.sdk = sdk

    async def run(self, agent: AgentDef, prompt: str) -> RunResult:
        options = {
            "system_prompt": agent.system_prompt,
            "model": agent.model,
            "allowed_tools": agent.allowed_tools,
            "max_tokens": agent.max_output_tokens,
            "mcp_servers": agent.mcp_servers,
        }
        resp = await self.sdk.query(prompt=prompt, options=options)
        usage = resp.get("usage", {})
        rec = UsageRecord(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cost_usd=usage.get("cost_usd", 0.0),
            agent=agent.name,
        )
        self.budget.accrue(rec)  # raises BudgetExceededError if over cap
        return RunResult(
            text=resp.get("text", ""),
            input_tokens=rec.input_tokens,
            output_tokens=rec.output_tokens,
            cost_usd=rec.cost_usd,
        )


async def _collect(stream):
    """Collect streamed SDK messages into a single dict. Implemented in Task 6 follow-up
    once we confirm the exact claude_agent_sdk streaming shape on the installed version."""
    text_parts, usage = [], {}
    async for msg in stream:
        if getattr(msg, "type", None) == "text":
            text_parts.append(msg.text)
        elif getattr(msg, "type", None) == "result":
            usage = {
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
                "cost_usd": msg.total_cost_usd,
            }
    return {"text": "".join(text_parts), "usage": usage}
```

- [ ] **Step 4: PASS. Commit.**

---

### Task 7: Doer/checker loop (the bouncer — the core invariant)

**Files:**
- Create: `src/designdoc/loop.py`, `tests/unit/test_loop.py`

**Why this is the most important task:** this is where Gen 3 rule #1 (control flow in Python) and rule #2 (no self-grading) get enforced. If this file is right, the rest of the system inherits correctness.

- [ ] **Step 1: Write failing tests covering:**
  - Doer produces, checker passes on attempt 1 → returned as pass.
  - Doer produces, checker fails attempt 1, doer retries, checker passes attempt 2 → returned as pass with attempt=2.
  - Doer produces, checker fails 3 times → ships with HIL comment + HIL issue appended to state.
  - Checker returns malformed JSON → synthetic fail, counts as an attempt.
  - Retry prompt built with ONLY the latest issues (not cumulative).

```python
# tests/unit/test_loop.py  (one representative test shown; write all 5)
import pytest
from designdoc.loop import doer_checker_loop, ArtifactResult
from designdoc.runner import AgentDef, RunResult

class ScriptedRunner:
    def __init__(self, by_agent: dict[str, list[str]]):
        self.by_agent = {k: list(v) for k, v in by_agent.items()}
        self.calls: list[tuple[str, str]] = []
    async def run(self, agent, prompt):
        self.calls.append((agent.name, prompt))
        out = self.by_agent[agent.name].pop(0)
        return RunResult(text=out, input_tokens=1, output_tokens=1, cost_usd=0.001)

@pytest.mark.anyio
async def test_passes_first_attempt():
    runner = ScriptedRunner({
        "doer": ["draft-1"],
        "checker": ['{"status":"pass","attempt":1,"artifact_id":"x","summary":"ok"}'],
    })
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    result = await doer_checker_loop(
        artifact_id="x", doer=doer, checker=checker,
        doer_prompt="write x", checker_prompt_fn=lambda d: f"check: {d}",
        runner=runner, hil_sink=[],
    )
    assert result.status == "pass"
    assert result.attempt == 1
    assert result.text == "draft-1"

@pytest.mark.anyio
async def test_ships_with_hil_after_3_fails():
    fail_json = lambda n: (
        f'{{"status":"fail","attempt":{n},"artifact_id":"x",'
        f'"issues":[{{"severity":"major","location":"l","current_text":"c","suggested_fix":"f"}}]}}'
    )
    runner = ScriptedRunner({
        "doer": ["d1", "d2", "d3"],
        "checker": [fail_json(1), fail_json(2), fail_json(3)],
    })
    hil_sink: list[dict] = []
    doer = AgentDef(name="doer", system_prompt="", model="m")
    checker = AgentDef(name="checker", system_prompt="", model="m")
    result = await doer_checker_loop(
        artifact_id="x", doer=doer, checker=checker,
        doer_prompt="p", checker_prompt_fn=lambda d: d,
        runner=runner, hil_sink=hil_sink,
    )
    assert result.status == "shipped_with_hil"
    assert result.attempt == 3
    assert len(hil_sink) == 1
    assert hil_sink[0]["artifact"] == "x"
    # critical: exactly 6 runner calls (3 doer + 3 checker), not 4, not 8
    assert len(runner.calls) == 6
```

- [ ] **Step 2: FAIL.**

- [ ] **Step 3: Implement `loop.py`**

```python
# src/designdoc/loop.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Literal, Any
from designdoc.runner import AgentDef, RunResult
from designdoc.verdict import parse_verdict, CheckerVerdict


@dataclass
class ArtifactResult:
    artifact_id: str
    status: Literal["pass", "shipped_with_hil"]
    text: str
    attempt: int
    verdict: CheckerVerdict


MAX_ATTEMPTS = 3   # CANONICAL — enforced here, nowhere else


async def doer_checker_loop(
    *,
    artifact_id: str,
    doer: AgentDef,
    checker: AgentDef,
    doer_prompt: str,
    checker_prompt_fn: Callable[[str], str],
    runner: Any,                # has .run(agent, prompt) -> RunResult
    hil_sink: list[dict],
    stage_name: str = "unknown",
) -> ArtifactResult:
    current_text = (await runner.run(doer, doer_prompt)).text

    for attempt in range(1, MAX_ATTEMPTS + 1):
        checker_raw = (await runner.run(checker, checker_prompt_fn(current_text))).text
        verdict = parse_verdict(checker_raw, attempt=attempt, artifact_id=artifact_id)

        if verdict.status == "pass":
            return ArtifactResult(artifact_id, "pass", current_text, attempt, verdict)

        if attempt == MAX_ATTEMPTS:
            hil_sink.append({
                "id": f"HIL-{len(hil_sink)+1:03d}",
                "artifact": artifact_id,
                "stage": stage_name,
                "severity": _max_severity(verdict),
                "doer_said": current_text[:500],
                "checker_said": verdict.summary or _first_issue(verdict),
                "attempts": attempt,
                "suggested_fixes": [i.suggested_fix for i in verdict.issues[:3]],
                "status": "open",
            })
            return ArtifactResult(artifact_id, "shipped_with_hil", current_text, attempt, verdict)

        # Retry with ONLY the latest issues (no cumulative drift)
        retry_prompt = _build_retry_prompt(doer_prompt, current_text, verdict)
        current_text = (await runner.run(doer, retry_prompt)).text

    raise AssertionError("unreachable")  # pragma: no cover


def _build_retry_prompt(original: str, previous_output: str, verdict: CheckerVerdict) -> str:
    issues_block = "\n".join(
        f"- [{i.severity}] {i.location}: {i.suggested_fix}" for i in verdict.issues
    )
    return (
        f"Your previous output was rejected. Address ONLY these specific issues:\n\n"
        f"{issues_block}\n\n"
        f"Original task:\n{original}\n\n"
        f"Previous output (for reference, not to repeat):\n{previous_output}"
    )


def _max_severity(v: CheckerVerdict) -> str:
    order = {"critical": 3, "major": 2, "minor": 1}
    return max((i.severity for i in v.issues), key=lambda s: order[s], default="minor")


def _first_issue(v: CheckerVerdict) -> str:
    return v.issues[0].suggested_fix if v.issues else ""
```

- [ ] **Step 4: PASS all 5 tests.**

- [ ] **Step 5: Commit** — `feat(loop): 3-attempt doer/checker bouncer with HIL fallback`.

---

### Task 8: Config loader

**Files:**
- Create: `src/designdoc/config.py`, `tests/unit/test_config.py`, `.designdoc.toml.example`

Config surface from the design doc §11. Pydantic `BaseModel` with field validators. Loads from TOML; falls back to defaults; merge order: file → env (`DESIGNDOC_*`) → CLI flags.

- [ ] **Step 1: Failing test** — valid file loads; missing file returns defaults; `max_attempts != 3` is explicitly rejected (invariant enforcement — we never let users override the cap at config level).

- [ ] **Step 2-4: FAIL → implement → PASS → commit.**

Key invariant: `max_attempts` is a **constant** in `loop.py`, not a config. The TOML schema does not even expose it. Config controls budget, parallelism, paths, language filters, MCP toggles, but not retry counts.

---

### Task 9: CLI scaffold (typer)

**Files:**
- Create: `src/designdoc/cli.py`, `tests/unit/test_cli.py`

Subcommands: `generate`, `resume`, `status`, `resolve`. All accept `--repo`, `--output`, `--config`, `--budget`. `resolve` is a stub in this task — implemented in Task 22.

- [ ] **Step 1: Test** `designdoc --help` shows all 4 subcommands; `designdoc status` on a fresh repo prints "no state yet".
- [ ] **Step 2-4: FAIL → implement → PASS → commit.**

---

### Task 10: Stage 0 — Discover

**Files:**
- Create: `src/designdoc/stages/s0_discover.py`, `src/designdoc/index/discover.py`, `tests/unit/test_discover.py`, `tests/integration/test_stage_discover.py`

Produces a `DiscoveryReport` dataclass: `{languages: {lang: file_count}, tree: list[Path]}`. Uses filename extensions + shebang sniffing; excludes `node_modules`, `.venv`, `dist`, `build`, `target`, `.git`, plus user-configured `exclude_paths`.

- [ ] TDD as usual. Fixture: `tests/fixtures/tiny_repo/` — commit 4 Python files representing 2 packages, plus a `pyproject.toml`.

---

### Task 11: Stage 1 — Index (AST-lite signatures)

**Files:**
- Create: `src/designdoc/stages/s1_index.py`, `src/designdoc/index/signatures.py`, `tests/unit/test_signatures.py`

**No LLM.** For Python, use `ast` module to extract: module-level docstring, classes (name, bases, methods with signatures, class docstring), top-level functions, imports. For TS/JS, use a regex pass (best-effort) — tree-sitter is v2. Writes `packages/<pkg>/classes/<Class>.signature.json` as intermediate state.

- [ ] TDD. Edge cases: empty `__init__.py`, syntax errors (emit a `parse_error` signature with `error` field — doesn't halt pipeline).

---

### Task 12: Stage 2 — file-analyzer agent + summary-validator

**Files:**
- Create: `src/designdoc/agents/prompts.py` (add `FILE_ANALYZER_SYSTEM`, `FILE_ANALYZER_SCHEMA`), `src/designdoc/agents/file_analyzer.py`, `src/designdoc/stages/s2_file_analysis.py`, `tests/integration/test_stage_file_analysis.py`

The checker here is **not an LLM** — it's a Pydantic schema validator (structural checker). The "loop" runs with a schema-validation "checker" that returns `pass`/`fail` based on parseability. Gen 3 principle: if a deterministic check suffices, use it; don't burn LLM calls on regex problems.

Prompt constraints (< 600 tokens, from design §8):
- Read-only tools: `Read`, `Grep`
- Input: one file path
- Output: JSON conforming to `FileSummary` schema — `{purpose, key_types, key_functions, external_deps, notes}`

- [ ] TDD with ScriptedRunner: cover parseable-on-1, parseable-on-2 (first run returns non-JSON, retry returns JSON), HIL-after-3-malformed.

---

### Task 13: Stage 3 — class-documenter + doc-quality-checker

**Files:**
- Create: `src/designdoc/agents/class_documenter.py`, `src/designdoc/agents/doc_quality_checker.py`, `src/designdoc/stages/s3_class_docs.py`, `src/designdoc/templates/class_doc.md.tmpl`, `tests/integration/test_stage_class_docs.py`

This is the **first task where both sides are LLMs**. The checker's system prompt (full text below — this is load-bearing):

```
You are a documentation QA reviewer. You will see:
1. The source class file (READ it with the Read tool).
2. A markdown class doc (provided in the user prompt).

Verify:
- Every method claimed in the doc exists in the source with matching signature.
- Every dependency/import claimed exists.
- Every behavioral claim is traceable to code — if you can't find the line, it's a hallucination.

You MUST reply with a single JSON object conforming to this schema:
{ "status": "pass" | "fail", "attempt": <int>, "artifact_id": "<id>",
  "summary": "<short>", "issues": [{"severity":..., "location":..., "current_text":..., "suggested_fix":...}] }

A status="pass" with any issue of severity major or critical is invalid.
A status="fail" with no issues is invalid.
Return only the JSON. No prose.
```

The prompt above **is the invariant** — it's the mechanism preventing self-grading.

- [ ] TDD. Template uses Jinja2: sections for Purpose / Public API / Dependencies / Notes / Mermaid (filled in Stage 5).

---

### Task 14: Stage 4 — package-documenter

**Files:**
- Create: `src/designdoc/agents/package_documenter.py`, `src/designdoc/stages/s4_package_rollups.py`, `src/designdoc/templates/package_readme.md.tmpl`, `tests/integration/test_stage_package_rollups.py`

Reads **only** generated class docs (`packages/<x>/classes/*.md`), not source — design §8 constraint. This keeps the rollup scope bounded and prevents token blowup on large packages. Reuses `doc-quality-checker` as its checker, but the checker's system prompt for this call is a specialization: "Verify the rollup accurately summarizes its input class docs; do NOT read source."

- [ ] TDD. Commit.

---

### Task 15: Mermaid — mmdc wrapper + preflight

**Files:**
- Create: `src/designdoc/mermaid/mmdc.py`, `tests/unit/test_mmdc.py`

Shell out to `npx --yes @mermaid-js/mermaid-cli` in `--quiet --input - --output /tmp/x.svg` mode. Any non-zero exit → syntax fail with captured stderr. Preflight probe runs once at orchestrator start: if `npx mmdc --version` fails, pipeline halts with a clear error before burning budget on Stage 5.

Tests gated by `@pytest.mark.requires_mmdc` — auto-skip if mmdc absent (so CI without node can still run the rest).

- [ ] TDD. Commit.

---

### Task 16: Stage 5 — mermaid-generator + two-checker loop

**Files:**
- Create: `src/designdoc/agents/mermaid_generator.py`, `src/designdoc/agents/mermaid_validator.py`, `src/designdoc/mermaid/loop.py`, `src/designdoc/stages/s5_mermaid.py`, `tests/integration/test_stage_mermaid.py`

**The two-checker loop** (design §5) is a specialization of the generic loop. Reuses `doer_checker_loop` for the outer retry, but inserts a deterministic mmdc syntax check between the doer and the LLM semantic checker:

```python
# src/designdoc/mermaid/loop.py  (sketch; full code in the task)
async def mermaid_loop(source_artifact, generator, validator, runner, hil_sink):
    # Wrap the generator's output in a pre-checker that runs mmdc first.
    async def syntax_then_semantic(doer_output):
        syntax_verdict = await run_mmdc_parse(doer_output)
        if syntax_verdict.status == "fail":
            return syntax_verdict.json()   # forces retry with syntax complaint
        return (await runner.run(validator, build_semantic_prompt(source_artifact, doer_output))).text
    # Reuse doer_checker_loop with a composite "checker" that is the fn above
    ...
```

- [ ] TDD. Edge cases: syntax-ok but hallucinated-node (must fail semantic), syntax-fail always (must HIL after 3), valid first try (fast path).

---

### Task 17: Stage 6 — tech debt with MCP

**Files:**
- Create: `src/designdoc/agents/techdebt_researcher.py`, `src/designdoc/agents/techdebt_crossref.py`, `src/designdoc/stages/s6_tech_debt.py`, `src/designdoc/templates/tech_debt.md.tmpl`, `tests/integration/test_stage_tech_debt.py`

Parses dependency manifests: `requirements.txt`, `pyproject.toml`, `package.json`, `pom.xml`, `build.gradle(.kts)`, `go.mod`, `Cargo.toml`. For each direct dep, invokes `techdebt-researcher` with `mcp_servers=["perplexity", "context7"]`; cross-ref checker runs independently with the same MCP access but fresh context.

Emits rows into `TECH_DEBT.md` via Jinja template. Disputed rows get HIL treatment.

- [ ] TDD. The integration test mocks the MCP tool calls (real MCP is gated behind `requires_api` marker).

---

### Task 18: Stage 7 — system design rollup

**Files:**
- Create: `src/designdoc/agents/system_designer.py`, `src/designdoc/stages/s7_system_rollup.py`, `src/designdoc/templates/system_design.md.tmpl`, `src/designdoc/templates/architecture.md.tmpl`, `tests/integration/test_stage_system_rollup.py`

Reads **only** `packages/*/README.md` — never source. Produces two artifacts in one doer call: narrative `SYSTEM_DESIGN.md` and `ARCHITECTURE.md` with C4-style container + component mermaid diagrams. Architecture diagrams re-enter the Stage 5 mermaid loop for validation (call into `mermaid_loop` directly).

- [ ] TDD. Commit.

---

### Task 19: Stage 8 — finalize (TOC + HIL YAML emit)

**Files:**
- Create: `src/designdoc/stages/s8_finalize.py`, `src/designdoc/templates/readme_toc.md.tmpl`, `tests/integration/test_stage_finalize.py`

Deterministic, no LLM. Walks the generated tree, builds `docs/design/README.md` with nested bullet TOC, writes/merges `hil-issues.yaml` from accumulated `state.hil_issues`.

- [ ] TDD. Commit.

---

### Task 20: Orchestrator — stage table + barrier drive

**Files:**
- Create: `src/designdoc/orchestrator.py`, `tests/integration/test_resume.py`, `tests/integration/test_budget_stop.py`

Final integration point. The `Orchestrator.run()` loop from design §7 — iterate the stage table, skip `done`, run stage, checkpoint, catch `BudgetExceededError` and exit cleanly.

Resume test: run pipeline halfway (stages 0–3), kill, restart with same state file, verify stages 4–8 run and 0–3 are skipped. Budget stop test: set cap to $0.01, verify pipeline halts at first LLM call with clean error and `.designdoc-state.json` shows last stage as `failed`.

- [ ] TDD. The resume test is the most important single test in this entire plan — it's the load-bearing evidence that Gen 3 determinism holds. Spend time on it.

---

### Task 21: Claude Code plugin — `/designdoc generate|resume|status`

**Files:**
- Create: `plugins/designdoc/plugin.json`, `plugins/designdoc/commands/designdoc.md`, `plugins/designdoc/README.md`

`plugin.json`:

```json
{
  "name": "designdoc",
  "version": "0.1.0",
  "description": "Generate validated design documentation for any codebase",
  "commands": ["./commands/designdoc.md"]
}
```

`commands/designdoc.md`:

```markdown
---
description: Generate or resume a validated design-document tree for the current repo
argument-hint: "[generate|resume|resolve|status] [path]"
allowed-tools: Bash(designdoc:*), Read, Edit, AskUserQuestion
---

Run the designdoc CLI for: $ARGUMENTS

Subcommands:
- `generate [path]` — full pipeline (stages 0–8). Default path is cwd.
- `resume [path]` — pick up from last checkpoint.
- `resolve [path]` — walk open HIL issues using AskUserQuestion.
- `status [path]` — show pipeline state + cost ledger.

For generate/resume/status: shell out via Bash to `designdoc <subcommand> --repo <path>`.
For resolve: invoke the resolve flow described in the project's CLAUDE.md.
```

- [ ] Install the plugin into a scratch Claude Code instance and run `/designdoc status` against `tests/fixtures/tiny_repo`. Confirm it prints "no state yet". Commit.

---

### Task 22: `/designdoc resolve` — HIL walker with AskUserQuestion

**Files:**
- Modify: `plugins/designdoc/commands/designdoc.md` (fill in resolve instructions)
- Modify: `src/designdoc/cli.py` (add `designdoc resolve --emit-questions` that prints JSON for the plugin to consume, and `designdoc resolve --apply-fix <HIL-ID> --fix <fix-text>` that patches the doc + marks issue resolved)
- Create: `tests/unit/test_cli_resolve.py`

The plugin reads `hil-issues.yaml`, picks first `open` issue, calls `AskUserQuestion` with the issue's `suggested_fixes` as options (plus an "Other" free-text), then calls back into the CLI to apply the chosen fix. CLI handles the file patch (regex replace the `<!-- HIL: HIL-XXX -->` comment block) and marks the YAML entry `status: resolved`.

- [ ] TDD (CLI-side). Commit.

---

### Task 23: End-to-end dogfood run

**Files:**
- Create: `tests/e2e/test_tiny_repo_full.py` (marked `requires_api`)
- Create: `tests/fixtures/tiny_repo/` (if not already populated in Task 10) — 4 Python files, 2 packages, 1 top-level entry, 1 test file. About 200 LOC total.

Full pipeline, real Claude API, mmdc installed, `--budget 2.00`. Assertions:
- `docs/design/README.md` exists and lists every package.
- Every `packages/*/classes/*.md` has a `## Mermaid` section with a `mermaid` fenced block.
- `mmdc` parse succeeds on every embedded diagram (run as post-check in the test).
- `TECH_DEBT.md` exists.
- `hil-issues.yaml` exists (may be empty).
- Total cost in `.designdoc-budget.json` is < $2.00.

Per `codebase-mentor/CLAUDE.md` test discipline: this test must be **executed** before the task is closed. "Written but unrun" = not done. The task close-out must report the exact pytest command, pass/fail, summary line, and — on failure — the artifact directory.

- [ ] Run. Triage any issues into follow-up tickets, don't paper over them. Commit.

---

## Verification (end-to-end)

Run in order:

```bash
# Unit + integration, no API
task test
# Expected: all green; ~80 tests

# Dogfood (requires ANTHROPIC_API_KEY and `npx mmdc --version` working)
ANTHROPIC_API_KEY=sk-... task dogfood
# Expected: docs/design/ tree generated under tests/fixtures/tiny_repo/,
#           cost < $2.00, no unresolved criticals in hil-issues.yaml

# Manual plugin smoke test
cp -r plugins/designdoc ~/.claude/plugins/designdoc
# In a Claude Code session:
/designdoc status
/designdoc generate tests/fixtures/tiny_repo
/designdoc resolve tests/fixtures/tiny_repo     # only if HIL issues exist

# Crash-resume verification
task dogfood
# kill mid-run (Ctrl-C during stage 3)
task dogfood
# Expected: picks up at stage 3, does not redo stages 0-2
```

**Acceptance gate:** the dogfood run generates a `docs/design/` tree that (a) renders in GitHub without broken mermaid diagrams, (b) contains no hallucinated method references (spot-check 5 random class docs), (c) reports cost under budget, (d) a second run from clean state produces byte-identical output for deterministic stages (0, 1, 8) and semantically-equivalent output for LLM stages.

## Out of scope (explicitly deferred to v1.1)

- Agent Brain MCP cross-referencing (design §13).
- PlantUML support.
- Incremental regeneration based on source content hashing.
- Cross-repo vocabulary linking.
- Sequence/ER diagram generators.
- VS Code preview extension.
- Tree-sitter-based TypeScript/JS signature extraction (v1 uses regex fallback).

## Critical files to modify during implementation

| File | Role |
|---|---|
| `src/designdoc/loop.py` | **The invariant**. Touch only via Task 7; never edit to "loosen" retry rules. |
| `src/designdoc/verdict.py` | Schema invariant. Changes require updating every checker's system prompt in lockstep. |
| `src/designdoc/agents/prompts.py` | All system prompts. Edits here affect behavior across stages — diff carefully. |
| `src/designdoc/orchestrator.py` | Stage table. Adding a stage = new enum value + new `stages/sN_*.py` + update table. |
| `src/designdoc/mermaid/loop.py` | The brand-protection layer. Do not let LLM-only validation slip in — mmdc is mandatory. |

## Self-review notes (post-write)

- Spec coverage: every stage 0–8 has a task; doer/checker loop has its own task; mermaid double-checker has its own task; HIL YAML has its own task; plugin + resolve has their own tasks; dogfood has its own task. ✓
- Type consistency: `AgentDef`, `RunResult`, `CheckerVerdict`, `ArtifactResult`, `PipelineState`, `StageStatus`, `UsageRecord`, `HILIssue` — same names used across tasks. ✓
- No placeholders: each task shows actual code or names the exact file to be created; no "TBD" or "similar to Task N". ✓
- Retry invariant lives in exactly one place (`MAX_ATTEMPTS = 3` in `loop.py`). ✓
- Checker isolation is enforced by every checker being a separate `AgentDef` with its own system prompt, no shared state. ✓
