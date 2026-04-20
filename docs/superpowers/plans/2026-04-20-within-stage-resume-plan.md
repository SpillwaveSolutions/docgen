# Within-Stage Crash-Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v1.2.0 — within-stage crash-resume across Stages 2–6, atomic artifact writes, and graceful budget-cap halt with resumable state.

**Architecture:** Extend `PipelineState.artifact_index` from `dict[str, str]` to `dict[str, dict[str, str]]` carrying per-artifact input hashes; checkpoint after each artifact under an `asyncio.Lock`; write artifact files and the state JSON via atomic write-then-rename; orchestrator catches `BudgetExceededError` at the stage boundary, saves state, and returns cleanly instead of re-raising.

**Tech Stack:** Python 3.12+, typer, pydantic, anyio, pytest, `uv` for package management, ruff for lint/format, `task` (Go Task) as the command runner. Every commit runs `task ci` green locally before push.

**Design spec:** [`docs/superpowers/specs/2026-04-17-within-stage-resume-design.md`](../specs/2026-04-17-within-stage-resume-design.md).

**Working directory:** `/Users/richardhightower/clients/spillwave/src/docgen`. Branch from `main` (tagged `v1.1.0`).

---

## Ground rules for the engineer

Read these before Task 1. They are non-negotiable.

1. **TWRC discipline.** Test → Write → Run → Commit. Every task ends with `task ci` green and one commit. No "I'll commit later." No broken `main`.
2. **CI-parity.** Whatever runs in GitHub Actions must run locally via `task ci`. If you add a new test command to CI, mirror it in Taskfile.yml. If you add a new task ci step, mirror it in `.github/workflows/test.yml`.
3. **Never loosen the 3-attempt cap.** `MAX_ATTEMPTS = 3` in `src/designdoc/loop.py` is a constitutional invariant. Never expose it in config. The guard test `test_config_does_not_expose_max_attempts` must stay green.
4. **Never merge doer and checker contexts.** Doer scratchpad never reaches checker.
5. **Touch existing files minimally.** Don't refactor adjacent code. Don't add comments that explain what well-named identifiers already say.
6. **No placeholders in code comments** (no "TODO", no "for now"). If something is incomplete, finish it or split it into a follow-up task.

---

## File structure

### New files
- `src/designdoc/io_utils.py` — single function: `atomic_write(path, content)`.
- `tests/unit/test_io_utils.py` — atomic_write tests.
- `tests/unit/test_state_backcompat.py` — artifact_index shape migration tests.
- `tests/unit/test_state_lock.py` — concurrent save safety test.
- `tests/integration/test_stage2_resume.py` — mid-stage kill simulation for Stage 2.
- `tests/integration/test_stage3_resume.py` — mid-stage kill simulation for Stage 3.
- `tests/integration/test_stage4_resume.py` — mid-stage kill simulation for Stage 4.
- `tests/integration/test_stage5_resume.py` — mid-stage kill simulation for Stage 5.
- `tests/integration/test_stage6_resume.py` — mid-stage kill simulation for Stage 6.
- `tests/integration/test_budget_halt.py` — graceful budget halt flow.
- `tests/integration/test_orchestrator_checkpoint_logs.py` — log-line observability.
- `tests/e2e/test_resume_mid_stage.py` — E2E dogfood with injected crash (requires_api).

### Modified files
- `src/designdoc/state.py` — artifact_index type change, backcompat loader, state_lock, atomic state.save().
- `src/designdoc/stages/s2_file_analysis.py` — per-file checkpoint + atomic_write.
- `src/designdoc/stages/s3_class_docs.py` — per-class checkpoint + atomic_write.
- `src/designdoc/stages/s4_package_rollups.py` — per-package checkpoint + atomic_write.
- `src/designdoc/stages/s5_mermaid.py` — per-class-diagram checkpoint + atomic_write.
- `src/designdoc/stages/s6_tech_debt.py` — per-topic checkpoint + atomic_write.
- `src/designdoc/orchestrator.py` — graceful budget halt, checkpoint-count log lines.
- `src/designdoc/cli.py` — "budget exhausted, resume with `designdoc resume --budget ...`" message.
- `CHANGELOG.md` — v1.2.0 entry.
- `pyproject.toml` — version 1.1.0 → 1.2.0.
- `src/designdoc/__init__.py` — `__version__` → `1.2.0`.
- `plugins/designdoc/plugin.json` — version → `1.2.0`.

---

## Task 1: `atomic_write` helper

**Purpose:** Centralized write-to-tempfile-then-rename so no partial artifact ever lands on disk under SIGKILL.

**Files:**
- Create: `src/designdoc/io_utils.py`
- Test: `tests/unit/test_io_utils.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_io_utils.py`:

```python
"""Atomic write helper: tempfile-and-rename semantics under POSIX."""
from __future__ import annotations

from pathlib import Path

import pytest

from designdoc.io_utils import atomic_write


def test_atomic_write_creates_target(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    atomic_write(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    target.write_text("old")
    atomic_write(target, "new")
    assert target.read_text() == "new"


def test_atomic_write_leaves_no_tmp_on_success(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    atomic_write(target, "hello")
    tmp_siblings = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert tmp_siblings == []


def test_atomic_write_requires_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "out.md"
    with pytest.raises(FileNotFoundError):
        atomic_write(target, "hello")


def test_atomic_write_tmp_is_gone_after_replace(tmp_path: Path) -> None:
    """The .tmp file must not persist after the rename."""
    target = tmp_path / "out.md"
    atomic_write(target, "hello")
    tmp = target.with_suffix(target.suffix + ".tmp")
    assert not tmp.exists()
    assert target.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_io_utils.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'designdoc.io_utils'`

- [ ] **Step 3: Write minimal implementation**

Create `src/designdoc/io_utils.py`:

```python
"""Filesystem helpers with crash-safe semantics.

atomic_write writes to a sibling .tmp file then os.replace(): on POSIX this
is atomic. A SIGKILL between the two steps leaves either a partial .tmp
(ignored on next run) or a complete target — never a truncated target.
"""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_io_utils.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run full local CI**

Run: `task ci`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/atomic-write
git add src/designdoc/io_utils.py tests/unit/test_io_utils.py
git commit -m "feat(io): atomic_write helper for crash-safe artifact writes

Writes to <path>.tmp then os.replace() — POSIX-atomic rename so a
SIGKILL between the two steps never leaves a truncated file on disk.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `artifact_index` shape change + backcompat loader

**Purpose:** Extend `PipelineState.artifact_index` from `dict[str, str]` to `dict[str, dict[str, str]]` carrying `{"path": ..., "input_hash": ...}`. Backcompat loader migrates old-shape state files in-memory with empty `input_hash` — forces reprocessing without data loss.

**Files:**
- Modify: `src/designdoc/state.py`
- Test: `tests/unit/test_state_backcompat.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_state_backcompat.py`:

```python
"""artifact_index shape change + backcompat loader.

v1.2 moves artifact_index from {id: str_path} to {id: {"path": ..., "input_hash": ...}}.
Old state files still round-trip: the loader migrates string values to dict
form with empty input_hash, which will never match current hashes -> the
stage will reprocess, which is safe and matches pre-v1.2 behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

from designdoc.state import PipelineState, STATE_FILENAME


def test_new_shape_round_trips(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    s = PipelineState(target_repo=tmp_path, output_dir=output)
    s.artifact_index["class:Foo"] = {"path": "packages/x/Foo.md", "input_hash": "abc"}
    s.save()

    loaded = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    assert loaded.artifact_index == {
        "class:Foo": {"path": "packages/x/Foo.md", "input_hash": "abc"}
    }


def test_backcompat_loads_old_string_shape(tmp_path: Path) -> None:
    """An old (v1.1) state file with string-valued artifact_index loads without error."""
    output = tmp_path / "out"
    output.mkdir()
    old_state = {
        "target_repo": str(tmp_path),
        "output_dir": str(output),
        "current_stage": 3,
        "stages": {"class_docs": "done"},
        "total_retries": 0,
        "hil_issues": [],
        "artifact_index": {"class:Foo": "packages/x/Foo.md"},
        "prev_hashes": {},
        "rollup_hashes": {},
    }
    (output / STATE_FILENAME).write_text(json.dumps(old_state))

    loaded = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    assert loaded.artifact_index == {
        "class:Foo": {"path": "packages/x/Foo.md", "input_hash": ""}
    }


def test_backcompat_migrated_values_force_reprocess(tmp_path: Path) -> None:
    """Empty input_hash will never equal a real SHA1 — skip-check fails, stage reprocesses."""
    output = tmp_path / "out"
    output.mkdir()
    old_state = {
        "target_repo": str(tmp_path),
        "output_dir": str(output),
        "current_stage": 0,
        "stages": {},
        "total_retries": 0,
        "hil_issues": [],
        "artifact_index": {"class:Foo": "packages/x/Foo.md"},
        "prev_hashes": {},
        "rollup_hashes": {},
    }
    (output / STATE_FILENAME).write_text(json.dumps(old_state))

    loaded = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    entry = loaded.artifact_index["class:Foo"]
    # Pretend the current hash of the input is some real SHA.
    current_input_hash = "a" * 40
    assert entry["input_hash"] != current_input_hash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_state_backcompat.py -v`
Expected: 3 FAIL — the current loader returns string-valued `artifact_index`.

- [ ] **Step 3: Modify `src/designdoc/state.py`**

Change the `artifact_index` field type and the loader. The full file should now read:

```python
"""Resumable pipeline state.

Every stage transition checkpoints to <output_dir>/.designdoc-state.json. On
restart, the orchestrator skips any stage marked DONE — that's what makes a
crashed run picks-up-where-it-stopped.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
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
    # v1.2: each artifact_index entry carries the output path AND the SHA1
    # of its inputs. On resume, an artifact is skipped only if the current
    # input_hash matches the recorded one AND the output file exists.
    artifact_index: dict[str, dict[str, str]] = field(default_factory=dict)
    # prev_hashes: SHA1 map from the last SUCCESSFUL run (seeded by Stage 8).
    # Incremental stages compare current Stage-0 hashes against this to
    # decide which source files need re-analysis.
    prev_hashes: dict[str, str] = field(default_factory=dict)
    # rollup_hashes: per-artifact SHA1 of a stage's INPUTS, keyed by
    # artifact_id. Legacy v1.1 structure retained for cross-run skip logic
    # outside of artifact_index (e.g. stage7 system rollup).
    rollup_hashes: dict[str, str] = field(default_factory=dict)

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

    def unchanged_paths(self, current_hashes: dict[str, str]) -> set[str]:
        """Return relative paths whose current hash matches prev_hashes."""
        return {
            path
            for path, current_hash in current_hashes.items()
            if self.prev_hashes.get(path) == current_hash
        }

    @classmethod
    def load_or_new(cls, output_dir: Path, target_repo: Path) -> PipelineState:
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
                artifact_index=_migrate_artifact_index(d.get("artifact_index", {})),
                prev_hashes=d.get("prev_hashes", {}),
                rollup_hashes=d.get("rollup_hashes", {}),
            )
        return cls(target_repo=target_repo, output_dir=output_dir)


def _migrate_artifact_index(raw: dict) -> dict[str, dict[str, str]]:
    """v1.1 stored values as strings; v1.2 stores dicts with path+input_hash.

    Empty input_hash never matches a real SHA1 -> stage reprocesses, which
    is the same as old behavior. Safe migration, no data loss."""
    migrated: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            migrated[key] = {"path": value, "input_hash": ""}
        else:
            migrated[key] = dict(value)
    return migrated


# Module-level lock so concurrent asyncio gather-children serialize their
# JSON rewrites (not their LLM calls). Acquired ONLY around save().
state_lock = asyncio.Lock()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_state_backcompat.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run full local CI (catch existing-test regressions)**

Run: `task ci`
Expected: all green. Several stages reference `state.artifact_index[id]` expecting strings; the migration handles this for OLD state, but any stage code that WRITES strings needs updating in later tasks. The current unit and integration suites shouldn't hit a write-path because Tasks 4-8 will update the stages. If a test fails here, it's a genuine existing-code integration — investigate before proceeding. Expected failures are **only** in tests that write `artifact_index` entries as strings (none exist today; greenfield case).

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/state-artifact-index-v2
git add src/designdoc/state.py tests/unit/test_state_backcompat.py
git commit -m "feat(state): artifact_index carries input_hash + backcompat loader

Shape changes dict[str, str] -> dict[str, dict[str, str]]. Old state
files migrate in-memory with empty input_hash, which forces reprocess
(same as pre-v1.2 behavior). Also introduces module-level state_lock
for concurrent save safety in later tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: State save atomicity + concurrent-save lock test

**Purpose:** State file itself uses atomic_write so a SIGKILL mid-save-json never leaves a truncated JSON. Verify state_lock serializes concurrent saves.

**Files:**
- Modify: `src/designdoc/state.py` (2-line change to `save()`)
- Test: `tests/unit/test_state_lock.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_state_lock.py`:

```python
"""Concurrent save safety and atomic state.json writes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from designdoc.state import PipelineState, state_lock


@pytest.mark.anyio("asyncio")
async def test_concurrent_saves_under_lock_preserve_last_write(tmp_path: Path) -> None:
    """50 concurrent mutators + saves; final state contains all 50 entries."""
    output = tmp_path / "out"
    output.mkdir()
    s = PipelineState(target_repo=tmp_path, output_dir=output)

    async def mutator(i: int) -> None:
        async with state_lock:
            s.artifact_index[f"id-{i}"] = {"path": f"p{i}", "input_hash": f"h{i}"}
            s.save()

    await asyncio.gather(*[mutator(i) for i in range(50)])

    loaded = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    assert len(loaded.artifact_index) == 50
    for i in range(50):
        assert loaded.artifact_index[f"id-{i}"] == {"path": f"p{i}", "input_hash": f"h{i}"}


def test_save_uses_atomic_write_no_tmp_leftover(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    s = PipelineState(target_repo=tmp_path, output_dir=output)
    s.save()
    siblings = [p.name for p in output.iterdir()]
    assert ".designdoc-state.json" in siblings
    assert not any(name.endswith(".tmp") for name in siblings)


def test_save_json_is_valid_after_partial_tmp_left_behind(tmp_path: Path) -> None:
    """If a stale .tmp exists from an earlier crash, save() should still succeed.

    atomic_write's rename replaces the target; the leftover .tmp from a past
    crash is overwritten on the next save's tempfile step."""
    output = tmp_path / "out"
    output.mkdir()
    stale_tmp = output / ".designdoc-state.json.tmp"
    stale_tmp.write_text("GARBAGE")

    s = PipelineState(target_repo=tmp_path, output_dir=output)
    s.save()

    # Target is valid JSON.
    data = json.loads((output / ".designdoc-state.json").read_text())
    assert data["target_repo"] == str(tmp_path)
    # .tmp cleanly replaced (no longer GARBAGE, or gone entirely).
    if stale_tmp.exists():
        # If still present, it must contain the JSON that was about to be renamed.
        assert stale_tmp.read_text() != "GARBAGE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_state_lock.py -v`
Expected: `test_save_uses_atomic_write_no_tmp_leftover` may or may not fail depending on whether `atomic_write` is used by `save()` yet; the other two pass. If the atomic-write test fails, that's the gap to close.

Run also: `uv run pytest tests/unit/ -v --no-header`
Expected: all green except the atomic-write test.

- [ ] **Step 3: Modify `state.py` — make `save()` atomic**

In `src/designdoc/state.py`, replace the `save()` method body:

```python
    def save(self) -> None:
        from designdoc.io_utils import atomic_write

        self.output_dir.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["target_repo"] = str(self.target_repo)
        data["output_dir"] = str(self.output_dir)
        data["stages"] = {k: str(v) for k, v in self.stages.items()}
        atomic_write(self.state_path, json.dumps(data, indent=2))
```

The import is inline to avoid a circular-import risk if `io_utils` ever grows to reference `state`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_state_lock.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run full local CI**

Run: `task ci`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/designdoc/state.py tests/unit/test_state_lock.py
git commit -m "feat(state): save() uses atomic_write + concurrent-save lock test

SIGKILL mid-save no longer leaves truncated .designdoc-state.json. The
module-level state_lock (added in prior commit) serializes the 50-way
concurrent-save test to a deterministic final shape.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Stage 2 within-stage checkpoint

**Purpose:** Each per-file summary checkpoints into `artifact_index` and `stage2_summaries.json` as it completes. Crash mid-stage + rerun: only unprocessed files hit the LLM.

**Key subtlety:** Stage 2's output is ONE aggregated JSON (`stage2_summaries.json`). We keep that shape — on each completion, we rewrite the JSON atomically AND update `state.artifact_index`. On resume, we reload the partial JSON first.

**Files:**
- Modify: `src/designdoc/stages/s2_file_analysis.py`
- Test: `tests/integration/test_stage2_resume.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_stage2_resume.py`:

```python
"""Stage 2 within-stage resume: mid-stage raise + rerun = no duplicate LLM calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from designdoc.runner import ClaudeSDKRunner
from designdoc.stages import s1_index, s2_file_analysis
from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.state import PipelineState


def _seed_stage0_and_stage1(state: PipelineState, files: dict[str, str]) -> None:
    """Write stage0_discovery.json (hashes) and stage1 signatures.json."""
    hashes = {p: hashlib.sha1(c.encode()).hexdigest() for p, c in files.items()}
    (state.output_dir / STAGE0_FILENAME).write_text(
        json.dumps({"hashes": hashes, "languages": {"python": list(files)}})
    )
    sigs = [
        {"path": p, "classes": [], "functions": [], "imports": [], "parse_error": None}
        for p in files
    ]
    (state.output_dir / s1_index.OUTPUT_FILENAME).write_text(json.dumps(sigs))


class _StubRunner:
    """Minimal runner double — counts calls and returns a fixed valid summary."""
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, *, agent, prompt: str, **_ignored) -> str:
        self.calls.append(prompt)
        return json.dumps({
            "purpose": "stub",
            "key_types": [],
            "key_functions": [],
            "external_deps": [],
            "notes": "",
        })


@pytest.mark.anyio("asyncio")
async def test_stage2_checkpoints_after_each_file(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_stage0_and_stage1(state, {"a.py": "print(1)", "b.py": "print(2)"})

    runner = _StubRunner()
    await s2_file_analysis.run(state=state, runner=runner, parallelism=1)

    # Both files have artifact_index entries, each with a non-empty input_hash.
    assert "file:a.py" in state.artifact_index
    assert "file:b.py" in state.artifact_index
    assert state.artifact_index["file:a.py"]["input_hash"] != ""


@pytest.mark.anyio("asyncio")
async def test_stage2_rerun_skips_checkpointed_files(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_stage0_and_stage1(state, {"a.py": "print(1)", "b.py": "print(2)"})

    # First run: both files processed.
    runner1 = _StubRunner()
    await s2_file_analysis.run(state=state, runner=runner1, parallelism=1)
    assert len(runner1.calls) == 2

    # Reload state (simulates process death + rerun).
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    runner2 = _StubRunner()
    await s2_file_analysis.run(state=state2, runner=runner2, parallelism=1)
    # No LLM calls on the rerun — everything already checkpointed.
    assert runner2.calls == []


@pytest.mark.anyio("asyncio")
async def test_stage2_mid_stage_crash_resumes_cleanly(tmp_path: Path) -> None:
    """Simulate SIGKILL after 1 of 2 files: re-run processes ONLY the remaining file."""
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_stage0_and_stage1(state, {"a.py": "print(1)", "b.py": "print(2)"})

    class _KillAfterOne:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def run(self, *, agent, prompt: str, **_ignored) -> str:
            self.calls.append(prompt)
            if len(self.calls) == 2:
                raise KeyboardInterrupt("simulated SIGKILL mid-stage")
            return json.dumps({
                "purpose": "stub",
                "key_types": [],
                "key_functions": [],
                "external_deps": [],
                "notes": "",
            })

    crashing_runner = _KillAfterOne()
    with pytest.raises(KeyboardInterrupt):
        await s2_file_analysis.run(state=state, runner=crashing_runner, parallelism=1)

    # One file got checkpointed before the crash.
    assert len(state.artifact_index) == 1

    # Reload + re-run with a clean runner: only the un-checkpointed file fires.
    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    clean_runner = _StubRunner()
    await s2_file_analysis.run(state=state2, runner=clean_runner, parallelism=1)
    assert len(clean_runner.calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_stage2_resume.py -v`
Expected: FAIL — current Stage 2 doesn't update `artifact_index` per-file and doesn't skip on `input_hash` match.

- [ ] **Step 3: Modify `src/designdoc/stages/s2_file_analysis.py`**

Rewrite `run()` to checkpoint per file. Full replacement of the file:

```python
"""Stage 2: per-file summaries via file-analyzer + pydantic schema checker.

For each file in Stage 1's signature list, run the doer/schema loop to produce
a validated FileSummary. Results persist to stage2_summaries.json.

v1.2 within-stage resume: each completed file updates state.artifact_index
under state_lock and rewrites stage2_summaries.json atomically. A crash
mid-stage leaves a partial JSON + partial artifact_index; the rerun skips
any file whose input hash still matches.
"""

from __future__ import annotations

import asyncio
import json

from designdoc.agents.file_analyzer import FileSummary, build_prompt, make_file_analyzer
from designdoc.io_utils import atomic_write
from designdoc.loop import doer_schema_loop
from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.stages.s1_index import OUTPUT_FILENAME as STAGE1_FILENAME
from designdoc.state import PipelineState, StageStatus, state_lock

STAGE_NAME = "file_analysis"
OUTPUT_FILENAME = "stage2_summaries.json"


async def run(
    *,
    state: PipelineState,
    runner,
    doer_model: str = "claude-sonnet-4-6",
    parallelism: int = 1,
) -> dict[str, dict]:
    """Execute Stage 2. Returns {relative_path: summary_dict}."""
    stage1_path = state.output_dir / STAGE1_FILENAME
    if not stage1_path.exists():
        raise FileNotFoundError(f"stage 1 output missing ({stage1_path}); run stage 1 first")

    state.stages[STAGE_NAME] = StageStatus.RUNNING
    async with state_lock:
        state.save()

    signatures = json.loads(stage1_path.read_text())
    doer = make_file_analyzer(model=doer_model)

    current_hashes = _current_source_hashes(state)
    reusable = _load_reusable_summaries(state, current_hashes)

    # Load any partial summaries from a prior crashed run.
    existing_path = state.output_dir / OUTPUT_FILENAME
    results: dict[str, dict] = {}
    if existing_path.exists():
        try:
            results = json.loads(existing_path.read_text())
        except (json.JSONDecodeError, OSError):
            results = {}

    to_process: list[dict] = []
    for sig in signatures:
        path = sig["path"]
        if sig.get("parse_error"):
            continue
        if path in reusable:
            # v1.1 cross-run skip: source unchanged since last SUCCESSFUL run.
            results[path] = reusable[path]
            continue
        current_hash = current_hashes.get(path, "")
        artifact_id = f"file:{path}"
        prior = state.artifact_index.get(artifact_id, {})
        if (
            prior.get("input_hash") == current_hash
            and current_hash != ""
            and path in results
        ):
            # v1.2 within-stage skip: this file was checkpointed in a prior
            # (possibly crashed) run of THIS invocation. Nothing to do.
            continue
        to_process.append(sig)

    sem = asyncio.Semaphore(max(1, parallelism))

    async def _one(sig: dict) -> None:
        path = sig["path"]
        current_hash = current_hashes.get(path, "")
        async with sem:
            prompt = build_prompt(path, json.dumps(sig, indent=2))
            result = await doer_schema_loop(
                artifact_id=f"file:{path}",
                doer=doer,
                doer_prompt=prompt,
                schema_model=FileSummary,
                runner=runner,
                hil_sink=state.hil_issues,
                stage_name=STAGE_NAME,
            )
            summary = _parse_or_placeholder(result.text, path)

        async with state_lock:
            results[path] = summary
            atomic_write(
                state.output_dir / OUTPUT_FILENAME,
                json.dumps(results, indent=2),
            )
            state.artifact_index[f"file:{path}"] = {
                "path": OUTPUT_FILENAME,
                "input_hash": current_hash,
            }
            state.save()

    await asyncio.gather(*[_one(s) for s in to_process])

    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 3)
    async with state_lock:
        state.save()
    return results


def _current_source_hashes(state: PipelineState) -> dict[str, str]:
    """Load {path: sha} from stage0_discovery.json; empty dict on any failure."""
    stage0_path = state.output_dir / STAGE0_FILENAME
    if not stage0_path.exists():
        return {}
    try:
        return json.loads(stage0_path.read_text()).get("hashes") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _load_reusable_summaries(
    state: PipelineState, current_hashes: dict[str, str]
) -> dict[str, dict]:
    """Cross-run incremental: files whose hash matches prev_hashes AND whose
    prior summary survives in stage2_summaries.json."""
    summaries_path = state.output_dir / OUTPUT_FILENAME
    if not summaries_path.exists() or not state.prev_hashes:
        return {}
    try:
        prev_summaries = json.loads(summaries_path.read_text()) or {}
    except (json.JSONDecodeError, OSError):
        return {}
    unchanged = state.unchanged_paths(current_hashes)
    return {path: prev_summaries[path] for path in unchanged if path in prev_summaries}


def _parse_or_placeholder(text: str, path: str) -> dict:
    try:
        return FileSummary.model_validate_json(text).model_dump()
    except Exception:
        return {
            "purpose": f"(HIL: summary for {path} disputed — see hil-issues.yaml)",
            "key_types": [],
            "key_functions": [],
            "external_deps": [],
            "notes": "unresolved",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_stage2_resume.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run full local CI**

Run: `task ci`
Expected: all green. The existing Stage 2 tests (`test_stage_file_analysis.py`, `test_stage2_incremental.py`, `test_stage2_parallel.py`) should continue to pass — the observable behavior is unchanged, only the per-file checkpoint timing differs.

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/stage2-within-resume
git add src/designdoc/stages/s2_file_analysis.py tests/integration/test_stage2_resume.py
git commit -m "feat(stage2): per-file checkpoint for within-stage resume

Each file summary updates artifact_index + atomically rewrites
stage2_summaries.json under state_lock. Mid-stage crash + rerun hits
LLM only for files not already checkpointed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Stage 3 within-stage checkpoint

**Purpose:** Each per-class doc checkpoints into `artifact_index` and writes its markdown atomically. Same pattern as Stage 2 but one file per class.

**Files:**
- Modify: `src/designdoc/stages/s3_class_docs.py`
- Test: `tests/integration/test_stage3_resume.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_stage3_resume.py`:

```python
"""Stage 3 within-stage resume: per-class checkpoint survives mid-stage crash."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from designdoc.stages import s1_index, s3_class_docs
from designdoc.stages.s0_discover import OUTPUT_FILENAME as STAGE0_FILENAME
from designdoc.state import PipelineState


def _seed(state: PipelineState, classes_per_file: dict[str, list[str]]) -> None:
    """Write stage0 hashes and stage1 signatures with the given classes."""
    hashes = {
        path: hashlib.sha1(f"src:{path}".encode()).hexdigest()
        for path in classes_per_file
    }
    (state.output_dir / STAGE0_FILENAME).write_text(
        json.dumps({"hashes": hashes, "languages": {"python": list(classes_per_file)}})
    )
    sigs = [
        {
            "path": path,
            "classes": [{"name": cls, "methods": [], "bases": []} for cls in classes],
            "functions": [],
            "imports": [],
            "parse_error": None,
        }
        for path, classes in classes_per_file.items()
    ]
    (state.output_dir / s1_index.OUTPUT_FILENAME).write_text(json.dumps(sigs))


class _StubRunner:
    def __init__(self, fail_after: int | None = None) -> None:
        self.calls: list[str] = []
        self.fail_after = fail_after

    async def run(self, *, agent, prompt: str, **_ignored) -> str:
        self.calls.append(prompt)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise KeyboardInterrupt("simulated SIGKILL")
        # Return minimal markdown for the doer; checker will also invoke
        # us — for checker prompts we return an approve verdict.
        if "doc-quality-checker" in (agent.system_prompt if hasattr(agent, "system_prompt") else ""):
            return json.dumps({"verdict": "pass", "reasoning": "stub"})
        return "# Stub class doc\n\nplaceholder"


@pytest.mark.anyio("asyncio")
async def test_stage3_checkpoints_per_class(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed(state, {"src/foo.py": ["A", "B"]})

    runner = _StubRunner()
    await s3_class_docs.run(state=state, runner=runner, parallelism=1)

    assert "src/foo.py::A" in state.artifact_index
    assert "src/foo.py::B" in state.artifact_index
    assert state.artifact_index["src/foo.py::A"]["input_hash"] != ""


@pytest.mark.anyio("asyncio")
async def test_stage3_rerun_skips_checkpointed_classes(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed(state, {"src/foo.py": ["A", "B"]})

    r1 = _StubRunner()
    await s3_class_docs.run(state=state, runner=r1, parallelism=1)
    first_call_count = len(r1.calls)
    assert first_call_count > 0

    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    r2 = _StubRunner()
    await s3_class_docs.run(state=state2, runner=r2, parallelism=1)
    assert r2.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_stage3_resume.py -v`
Expected: FAIL — Stage 3's current `artifact_index` writes are string-shaped, and there's no `input_hash` gate on resume.

- [ ] **Step 3: Modify `src/designdoc/stages/s3_class_docs.py`**

In the existing file, find the section inside `_one()` that writes the output and updates `state.artifact_index`. Replace the `async def _one(...)` body with:

```python
    async def _one(sig: dict, cls: dict) -> None:
        class_id = f"{sig['path']}::{cls['name']}"
        out_path = _class_doc_path(state.output_dir, sig["path"], cls["name"])
        current_input_hash = _class_input_hash(
            source_sha=current_source_hashes.get(sig["path"], ""),
            class_signature=cls,
        )

        # v1.2 within-stage skip: if we already produced this class with
        # the same inputs AND the doc exists on disk, no LLM call.
        prior = state.artifact_index.get(class_id, {})
        if (
            prior.get("input_hash") == current_input_hash
            and current_input_hash != ""
            and out_path.exists()
        ):
            return

        async with sem:
            doer_prompt = build_class_prompt(
                class_name=cls["name"],
                source_path=sig["path"],
                signature_json=json.dumps(cls, indent=2),
            )

            def checker_prompt_fn(doc: str, *, _cls=cls, _sig=sig) -> str:
                return build_checker_prompt(
                    class_name=_cls["name"],
                    source_path=_sig["path"],
                    doc_markdown=doc,
                )

            result = await doer_checker_loop(
                artifact_id=class_id,
                doer=doer,
                checker=checker,
                doer_prompt=doer_prompt,
                checker_prompt_fn=checker_prompt_fn,
                runner=runner,
                hil_sink=state.hil_issues,
                stage_name=STAGE_NAME,
            )
            content = result.text
            if result.status == "shipped_with_hil":
                hil_id = state.hil_issues[-1]["id"]
                content = (
                    f"{inline_comment(hil_id, 'doc-quality checker disputed claims')}\n\n"
                    + content
                )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(out_path, content)
            rel = str(out_path.relative_to(state.output_dir))

        async with state_lock:
            state.artifact_index[class_id] = {
                "path": rel,
                "input_hash": current_input_hash,
            }
            state.save()
```

Above this, just before the `sem = asyncio.Semaphore(...)` line, compute the current source hashes once:

```python
    current_source_hashes = _current_source_hashes(state)
```

Remove the existing cross-run `if source_unchanged and out_path.exists():` branch from the `for sig in signatures:` pre-loop — the unified within-stage skip in `_one()` already covers it. Replace the pre-loop block with:

```python
    to_process: list[tuple[dict, dict]] = []
    for sig in signatures:
        if sig.get("parse_error") or not sig.get("classes"):
            continue
        for cls in sig["classes"]:
            to_process.append((sig, cls))
```

At the top of the file, add the imports:

```python
from designdoc.io_utils import atomic_write
from designdoc.state import PipelineState, StageStatus, state_lock
```

(replace the existing `from designdoc.state import PipelineState, StageStatus` line.)

Replace the module-level `_unchanged_source_paths` helper with `_current_source_hashes` (since skip is now per-class not per-file) and add the hash composition:

```python
import hashlib


def _current_source_hashes(state: PipelineState) -> dict[str, str]:
    stage0_path = state.output_dir / STAGE0_FILENAME
    if not stage0_path.exists():
        return {}
    try:
        return json.loads(stage0_path.read_text()).get("hashes") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _class_input_hash(source_sha: str, class_signature: dict) -> str:
    """Per-class input hash: source SHA + canonical JSON of the signature."""
    if not source_sha:
        return ""
    h = hashlib.sha1()
    h.update(source_sha.encode())
    h.update(json.dumps(class_signature, sort_keys=True).encode())
    return h.hexdigest()
```

Drop the existing `await asyncio.gather(*[_one(s, c) for s, c in to_process])` line that rebinds to a `(class_id, rel)` tuple — since `_one` now returns None, just gather without unpacking:

```python
    await asyncio.gather(*[_one(s, c) for s, c in to_process])
```

Replace the final stage-done block:

```python
    state.stages[STAGE_NAME] = StageStatus.DONE
    state.current_stage = max(state.current_stage, 4)
    async with state_lock:
        state.save()

    # Rebuild the written mapping from artifact_index for the return value.
    written = {
        class_id: entry["path"]
        for class_id, entry in state.artifact_index.items()
        if class_id.startswith(tuple(f"{s['path']}::" for s in signatures))
    }
    return written
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_stage3_resume.py tests/integration/test_stage3_incremental.py tests/integration/test_stage_class_docs.py -v`
Expected: all passed.

- [ ] **Step 5: Run full local CI**

Run: `task ci`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/stage3-within-resume
git add src/designdoc/stages/s3_class_docs.py tests/integration/test_stage3_resume.py
git commit -m "feat(stage3): per-class checkpoint for within-stage resume

Each class doc updates artifact_index with its composite input_hash
(source SHA + canonical signature JSON) and writes the markdown via
atomic_write. Mid-stage crash + rerun skips every class whose inputs
still match the checkpoint.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Stage 4 within-stage checkpoint

**Purpose:** Per-package rollup skip check uses `artifact_index` + rollup input hash. Each package README writes atomically.

**Files:**
- Modify: `src/designdoc/stages/s4_package_rollups.py`
- Test: `tests/integration/test_stage4_resume.py`

- [ ] **Step 1: Read Stage 4's current shape first**

Run: `uv run python -c "from designdoc.stages import s4_package_rollups; print(s4_package_rollups.__file__)"`
Then read it: `Read(path)` (no code shown here — the engineer inspects the current file before modifying).

- [ ] **Step 2: Write the failing test**

Create `tests/integration/test_stage4_resume.py`:

```python
"""Stage 4 within-stage resume: per-package checkpoint."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.stages import s4_package_rollups
from designdoc.state import PipelineState


class _StubRunner:
    def __init__(self) -> None:
        self.calls = []

    async def run(self, *, agent, prompt, **_ignored):
        self.calls.append(prompt)
        if "checker" in (getattr(agent, "system_prompt", "") or "").lower():
            return json.dumps({"verdict": "pass", "reasoning": "stub"})
        return "# Package README\n\nstub"


def _seed_class_docs(output: Path, pkg: str, classes: list[str]) -> None:
    pkg_dir = output / "packages" / pkg / "classes"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    for c in classes:
        (pkg_dir / f"{c}.md").write_text(f"# {c}\n\nclass doc")


def _seed_artifact_index(state: PipelineState, pkg: str, classes: list[str]) -> None:
    for c in classes:
        class_id = f"src/{pkg}/{c.lower()}.py::{c}"
        state.artifact_index[class_id] = {
            "path": f"packages/{pkg}/classes/{c}.md",
            "input_hash": f"hash-{c}",
        }


@pytest.mark.anyio("asyncio")
async def test_stage4_rerun_skips_checkpointed_packages(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    _seed_class_docs(output, "alpha", ["A1", "A2"])
    _seed_artifact_index(state, "alpha", ["A1", "A2"])

    r1 = _StubRunner()
    await s4_package_rollups.run(state=state, runner=r1, parallelism=1)
    first_count = len(r1.calls)
    assert first_count > 0

    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    # Re-seed artifact_index since state was persisted — still pointing at same hashes.
    r2 = _StubRunner()
    await s4_package_rollups.run(state=state2, runner=r2, parallelism=1)
    assert r2.calls == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_stage4_resume.py -v`
Expected: FAIL — Stage 4 currently writes its rollup-hash check against `rollup_hashes`, not per-artifact `artifact_index` entries with `package:<name>` ids.

- [ ] **Step 4: Modify `src/designdoc/stages/s4_package_rollups.py`**

In the per-package inner loop, apply the same pattern as Stage 3:

1. Compute `current_input_hash = hashlib.sha1(concat-of-sorted-class-doc-hashes).hexdigest()` where each class doc hash comes from `state.artifact_index[class_id]["input_hash"]`.
2. Check `prior = state.artifact_index.get(f"package:{pkg}")`.
3. If `prior.get("input_hash") == current_input_hash and out_path.exists()`, skip.
4. Otherwise process, atomic_write the README, checkpoint under `state_lock`.

Add imports:

```python
import hashlib
from designdoc.io_utils import atomic_write
from designdoc.state import state_lock
```

(Exact line-edit-level instructions: the engineer reads the current file, finds the per-package loop, and adapts it following the same shape as Task 5's Stage 3 `_one()` replacement. The change is structurally identical — only the ID prefix (`package:` vs `file::Class`) and the hash composition differ.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_stage4_resume.py tests/integration/test_stage4_incremental.py tests/integration/test_stage_package_rollups.py -v`
Expected: all passed.

- [ ] **Step 6: Run full local CI**

Run: `task ci`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git checkout -b feat/stage4-within-resume
git add src/designdoc/stages/s4_package_rollups.py tests/integration/test_stage4_resume.py
git commit -m "feat(stage4): per-package checkpoint for within-stage resume

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Stage 5 within-stage checkpoint

**Purpose:** Each per-class mermaid diagram checkpoints. The mermaid two-checker loop (mmdc + LLM semantic) is unchanged; only the surrounding per-class skip-and-save pattern is added.

**Files:**
- Modify: `src/designdoc/stages/s5_mermaid.py`
- Test: `tests/integration/test_stage5_resume.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_stage5_resume.py`:

```python
"""Stage 5 within-stage resume: per-class diagram checkpoint."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.stages import s5_mermaid
from designdoc.state import PipelineState


@pytest.mark.requires_mmdc
@pytest.mark.anyio("asyncio")
async def test_stage5_rerun_skips_checkpointed_diagrams(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "packages" / "a" / "classes").mkdir(parents=True)
    class_doc = output / "packages" / "a" / "classes" / "Foo.md"
    class_doc.write_text("# Foo\n\nclass doc")

    state = PipelineState(target_repo=tmp_path, output_dir=output)
    state.artifact_index["src/a/foo.py::Foo"] = {
        "path": "packages/a/classes/Foo.md",
        "input_hash": "hash-foo",
    }

    class _StubRunner:
        def __init__(self) -> None:
            self.calls = []

        async def run(self, *, agent, prompt, **_ignored):
            self.calls.append(prompt)
            return "```mermaid\nclassDiagram\nclass Foo\n```"

    r1 = _StubRunner()
    await s5_mermaid.run(state=state, runner=r1, parallelism=1)
    first = len(r1.calls)
    assert first > 0

    state2 = PipelineState.load_or_new(output_dir=output, target_repo=tmp_path)
    state2.artifact_index["src/a/foo.py::Foo"] = state.artifact_index["src/a/foo.py::Foo"]
    r2 = _StubRunner()
    await s5_mermaid.run(state=state2, runner=r2, parallelism=1)
    assert r2.calls == []
```

- [ ] **Step 2-6: Follow the same pattern as Tasks 5 and 6**

- In `_one()` per-class path: compute `current_input_hash` = the class's `artifact_index[class_id]["input_hash"]`. The diagram's input IS the class doc's input.
- Use `f"mermaid:{class_id}"` as the artifact_id.
- Skip if `prior.get("input_hash") == current_input_hash and out_path.exists()`.
- Write via `atomic_write`, checkpoint under `state_lock`.

Run: `uv run pytest tests/integration/test_stage5_resume.py tests/integration/test_stage5_incremental.py tests/integration/test_stage_mermaid.py -v`
Expected: all passed.

Run: `task ci`
Expected: all green.

Commit:

```bash
git checkout -b feat/stage5-within-resume
git add src/designdoc/stages/s5_mermaid.py tests/integration/test_stage5_resume.py
git commit -m "feat(stage5): per-class mermaid checkpoint for within-stage resume

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Stage 6 within-stage checkpoint

**Purpose:** Per-topic tech-debt research checkpoints independently.

**Files:**
- Modify: `src/designdoc/stages/s6_tech_debt.py`
- Test: `tests/integration/test_stage6_resume.py`

- [ ] **Steps identical to Task 6, with `topic:<name>` as the artifact_id and the dep-manifest-entry SHA as the `input_hash`.**

- [ ] **Test, then CI, then commit.**

```bash
git checkout -b feat/stage6-within-resume
git add src/designdoc/stages/s6_tech_debt.py tests/integration/test_stage6_resume.py
git commit -m "feat(stage6): per-topic checkpoint for within-stage resume

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Orchestrator graceful budget halt + CLI message

**Purpose:** When a stage raises `BudgetExceededError`, orchestrator marks state, saves, and returns a halt signal. CLI prints "budget exhausted — resume with `designdoc resume --budget <new-cap>`" and exits 0.

**Files:**
- Modify: `src/designdoc/orchestrator.py`
- Modify: `src/designdoc/cli.py`
- Test: `tests/integration/test_budget_halt.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_budget_halt.py`:

```python
"""Graceful budget halt: mid-stage cap exit 0, resume --budget picks up."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from designdoc.budget import BudgetExceededError, CostAccumulator
from designdoc.orchestrator import Orchestrator, StageEntry
from designdoc.state import PipelineState, StageStatus


class _AlwaysBudget:
    """Stage that immediately raises BudgetExceededError."""
    async def run(self, **_kwargs) -> None:
        raise BudgetExceededError("stub: cap exceeded")


@pytest.mark.anyio("asyncio")
async def test_orchestrator_catches_budget_and_returns_halted(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    budget = CostAccumulator(cap_usd=1.0, path=output / ".designdoc-budget.json")
    stage = _AlwaysBudget()

    orch = Orchestrator(
        state=state,
        runner=None,
        budget=budget,
        stages=[StageEntry("fake", stage.run, needs_runner=False)],
    )
    # Instead of raising, the orchestrator returns None and marks FAILED.
    await orch.run()
    assert state.stages["fake"] == StageStatus.FAILED
    assert state.halted_on_budget is True


@pytest.mark.anyio("asyncio")
async def test_orchestrator_completed_has_halted_on_budget_false(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    budget = CostAccumulator(cap_usd=1.0, path=output / ".designdoc-budget.json")

    class _Noop:
        async def run(self, **_kwargs) -> None:
            return

    orch = Orchestrator(
        state=state,
        runner=None,
        budget=budget,
        stages=[StageEntry("noop", _Noop().run, needs_runner=False)],
    )
    await orch.run()
    assert state.halted_on_budget is False
```

- [ ] **Step 2: Run test — expect FAIL on `state.halted_on_budget` (field doesn't exist yet)**

Run: `uv run pytest tests/integration/test_budget_halt.py -v`
Expected: FAIL with AttributeError on `state.halted_on_budget`.

- [ ] **Step 3: Add `halted_on_budget` to `PipelineState`**

In `src/designdoc/state.py`, add the field:

```python
    halted_on_budget: bool = False
```

Also persist and load it in `save()` / `load_or_new` (asdict covers save; add `halted_on_budget=d.get("halted_on_budget", False)` to the loader).

- [ ] **Step 4: Modify `Orchestrator.run()` to catch BudgetExceededError cleanly**

In `src/designdoc/orchestrator.py`, replace the `except BudgetExceededError:` block with:

```python
            except BudgetExceededError:
                self.state.stages[entry.name] = StageStatus.FAILED
                self.state.halted_on_budget = True
                self.state.save()
                self.budget.save()
                log.info(
                    "[%d/%d] stage %s halted after %.1fs (budget exceeded) — "
                    "run `designdoc resume --budget <new-cap>` to continue",
                    idx,
                    total,
                    entry.name,
                    time.monotonic() - start,
                )
                return
```

Note: `return` instead of `raise`. The orchestrator no longer propagates the error. State is persisted so the CLI can check `state.halted_on_budget`.

- [ ] **Step 5: Modify CLI to format the message and exit 0**

In `src/designdoc/cli.py`, `generate()`:

Replace the existing `except BudgetExceededError as e:` block — BudgetExceededError is no longer raised. After `anyio.run(...)` returns, check the state:

```python
    try:
        anyio.run(_run_orchestrator, repo_p, output, budget, skip_set, config, parallelism)
    except MmdcNotAvailableError as e:
        typer.echo(f"mmdc preflight failed: {e}", err=True)
        typer.echo("Use --skip mermaid to proceed without mermaid diagrams.", err=True)
        raise typer.Exit(code=3) from e

    # Check for graceful budget halt.
    output_dir = _resolve_output(repo_p, output, load_config(config).output_dir)
    state = PipelineState.load_or_new(output_dir=output_dir, target_repo=repo_p)
    if state.halted_on_budget:
        budget_path = output_dir / BUDGET_FILENAME
        spent = "$0.00"
        cap = "$?.??"
        if budget_path.exists():
            data = json.loads(budget_path.read_text())
            spent = f"${data['total_cost_usd']:.2f}"
            cap = f"${data['cap_usd']:.2f}"
        typer.echo(
            f"budget exhausted at {spent} / cap {cap}.",
            err=True,
        )
        typer.echo(
            "Run `designdoc resume --budget <new-cap>` to continue.",
            err=True,
        )
        # Exit 0 per spec: pipeline is resumable, no user action required
        # beyond the suggested command.
        raise typer.Exit(code=0)
```

Add `import json` at the top of `cli.py` if not already present.

Also: on successful resume with `--budget`, reset `state.halted_on_budget = False` before the orchestrator loop starts. Add this inside `_run_orchestrator`:

```python
    state = PipelineState.load_or_new(output_dir=output, target_repo=repo)
    # Budget overrides reset the halt flag so a successful rerun clears it.
    if budget_usd is not None:
        state.halted_on_budget = False
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/integration/test_budget_halt.py -v`
Expected: 2 passed.

Run full Stage 3 budget test in the existing suite: `uv run pytest tests/integration/test_resume.py::test_budget_exceeded_halts_pipeline -v`
Expected: passes. If it now fails because the old test asserts `BudgetExceededError` is raised, update the old test to assert the new behavior (state.halted_on_budget == True, stage marked FAILED, orchestrator returned cleanly).

- [ ] **Step 7: Run full local CI**

Run: `task ci`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git checkout -b feat/graceful-budget-halt
git add src/designdoc/orchestrator.py src/designdoc/cli.py src/designdoc/state.py \
        tests/integration/test_budget_halt.py tests/integration/test_resume.py
git commit -m "feat(orchestrator): graceful budget halt with resumable state

Budget exceeded mid-stage now sets state.halted_on_budget, marks the
stage FAILED, and returns cleanly. CLI catches the flag, prints
'budget exhausted at \$X / cap \$Y — resume --budget <new-cap>', and
exits 0 (per design spec).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Checkpoint-count observability in orchestrator logs

**Purpose:** `[N/9] stage x starting` becomes `[N/9] class_docs: 40/60 artifacts checkpointed, processing 20 remaining`.

**Files:**
- Modify: `src/designdoc/orchestrator.py`
- Test: `tests/integration/test_orchestrator_checkpoint_logs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_orchestrator_checkpoint_logs.py`:

```python
"""Orchestrator log lines surface checkpoint counts per stage."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from designdoc.budget import CostAccumulator
from designdoc.orchestrator import Orchestrator, StageEntry
from designdoc.state import PipelineState


@pytest.mark.anyio("asyncio")
async def test_stage_log_includes_checkpoint_counts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    state = PipelineState(target_repo=tmp_path, output_dir=output)
    # Seed 3 checkpointed class_docs artifacts.
    for i in range(3):
        state.artifact_index[f"class:C{i}"] = {"path": f"x/C{i}.md", "input_hash": f"h{i}"}
    budget = CostAccumulator(cap_usd=10.0, path=output / ".designdoc-budget.json")

    async def fake_stage(**_kwargs):
        return None

    orch = Orchestrator(
        state=state,
        runner=None,
        budget=budget,
        stages=[StageEntry("class_docs", fake_stage, needs_runner=False)],
    )
    with caplog.at_level(logging.INFO):
        await orch.run()

    matching = [r.getMessage() for r in caplog.records if "class_docs" in r.getMessage()]
    starting = next((m for m in matching if "starting" in m or "checkpointed" in m), None)
    assert starting is not None, matching
    # The starting line should report the 3 pre-existing checkpoints.
    assert "3" in starting
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_orchestrator_checkpoint_logs.py -v`
Expected: FAIL — current log says "stage class_docs starting" without a count.

- [ ] **Step 3: Modify `orchestrator.py` starting-log line**

Replace the existing:

```python
            log.info("[%d/%d] stage %s starting", idx, total, entry.name)
```

With:

```python
            prefix = f"{entry.name}:"
            prior_ids = [
                id_
                for id_ in self.state.artifact_index
                if id_.startswith(prefix) or self._id_belongs_to_stage(id_, entry.name)
            ]
            count = len(prior_ids)
            if count > 0:
                log.info(
                    "[%d/%d] stage %s: %d artifacts checkpointed",
                    idx, total, entry.name, count,
                )
            else:
                log.info("[%d/%d] stage %s starting", idx, total, entry.name)
```

Add the helper method to `Orchestrator`:

```python
    @staticmethod
    def _id_belongs_to_stage(artifact_id: str, stage_name: str) -> bool:
        """Map artifact_id prefixes to stages."""
        mapping = {
            "file_analysis": "file:",
            "class_docs": lambda id_: "::" in id_ and not id_.startswith(("file:", "package:", "mermaid:", "topic:")),
            "package_rollups": "package:",
            "mermaid": "mermaid:",
            "tech_debt": "topic:",
        }
        rule = mapping.get(stage_name)
        if rule is None:
            return False
        if isinstance(rule, str):
            return artifact_id.startswith(rule)
        return rule(artifact_id)
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/integration/test_orchestrator_checkpoint_logs.py -v`
Expected: passes.

- [ ] **Step 5: Full CI**

Run: `task ci`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/orch-checkpoint-logs
git add src/designdoc/orchestrator.py tests/integration/test_orchestrator_checkpoint_logs.py
git commit -m "feat(orchestrator): log prior-checkpoint counts per stage

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: E2E mid-stage resume test (requires_api, gated)

**Purpose:** Real pipeline run on `tiny_repo`, injected raise mid-Stage-3, resume, assert final doc tree is byte-identical to a clean cold run.

**Files:**
- Create: `tests/e2e/test_resume_mid_stage.py`

- [ ] **Step 1: Write the test**

```python
"""E2E within-stage resume. Requires `claude` CLI. Costs real money."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest


def _claude_available() -> bool:
    try:
        subprocess.run(
            ["claude", "--version"], check=True, capture_output=True, timeout=10
        )
        return True
    except Exception:
        return False


pytestmark = pytest.mark.requires_api


def _tree_hash(root: Path) -> str:
    """SHA1 of sorted (relpath, content-sha1) pairs — byte-identical check."""
    entries = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".designdoc-" not in p.name:
            content_sha = hashlib.sha1(p.read_bytes()).hexdigest()
            entries.append(f"{p.relative_to(root)}:{content_sha}")
    return hashlib.sha1("\n".join(entries).encode()).hexdigest()


def test_mid_stage_kill_resumes_to_identical_tree(tmp_path: Path) -> None:
    if not _claude_available():
        pytest.skip("claude CLI not logged in")

    fixture = Path(__file__).parent.parent / "fixtures" / "tiny_repo"
    clean_output = tmp_path / "clean"
    resumed_output = tmp_path / "resumed"

    # Clean cold run.
    subprocess.run(
        ["uv", "run", "designdoc", "generate",
         "--repo", str(fixture), "--output", str(clean_output), "--budget", "10"],
        check=True, timeout=1800,
    )
    clean_hash = _tree_hash(clean_output)

    # Crash-resume run.
    proc = subprocess.Popen(
        ["uv", "run", "designdoc", "generate",
         "--repo", str(fixture), "--output", str(resumed_output), "--budget", "10"],
    )
    # Crude crash: kill after 30s (Stage 3 should be in progress on tiny_repo).
    import time as _t
    _t.sleep(30)
    proc.kill()
    proc.wait(timeout=10)

    # Resume.
    subprocess.run(
        ["uv", "run", "designdoc", "resume",
         "--repo", str(fixture), "--output", str(resumed_output)],
        check=True, timeout=1800,
    )
    resumed_hash = _tree_hash(resumed_output)

    assert clean_hash == resumed_hash
```

- [ ] **Step 2: Run test (gated — will skip without `claude` CLI)**

Run: `uv run pytest tests/e2e/test_resume_mid_stage.py -v`
Expected: skipped OR passed (costs real money).

- [ ] **Step 3: Commit**

```bash
git checkout -b test/e2e-resume-mid-stage
git add tests/e2e/test_resume_mid_stage.py
git commit -m "test(e2e): within-stage resume produces byte-identical output

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: CHANGELOG + version bump + release close-out

**Purpose:** Mirror v1.1's close-out pattern: CHANGELOG entry, version bump across all three files, tag v1.2.0, push, GitHub release.

**Files:**
- Modify: `CHANGELOG.md` (prepend v1.2.0 section)
- Modify: `pyproject.toml` (version → 1.2.0)
- Modify: `src/designdoc/__init__.py` (`__version__ = "1.2.0"`)
- Modify: `plugins/designdoc/plugin.json` (version → 1.2.0)

- [ ] **Step 1: Prepend CHANGELOG entry**

Insert after the `## [1.1.0]` header block, at the top of the changelog body:

```markdown
## [1.2.0] - 2026-04-20

Within-stage crash-resume. A pipeline killed mid-Stage-N no longer loses
that stage's partial progress — the rerun skips any artifact whose
input hash still matches the checkpoint. Budget-cap halts are now
graceful exits with a resume-message format.

### Added

- **Within-stage checkpointing.** Stages 2 (file summaries), 3 (class
  docs), 4 (package rollups), 5 (mermaid), and 6 (tech-debt topics) now
  write to `state.artifact_index` after every completed artifact, under
  an `asyncio.Lock`. Mid-stage crash + rerun never re-calls the LLM for
  already-produced artifacts.
- **Atomic artifact writes.** New `designdoc.io_utils.atomic_write(path,
  content)` writes to a sibling `.tmp` then `os.replace()` (POSIX-atomic).
  Every artifact and the `.designdoc-state.json` file itself use it.
- **Graceful budget halt.** `BudgetExceededError` at a stage boundary
  now sets `state.halted_on_budget=True`, marks the stage FAILED, saves
  state, and the CLI prints "budget exhausted at $X / cap $Y — run
  `designdoc resume --budget <new-cap>` to continue" with exit 0.
- **Observability.** Orchestrator stage-start log lines include the
  count of already-checkpointed artifacts (e.g., `[3/9] stage class_docs:
  40 artifacts checkpointed`).

### Changed

- `PipelineState.artifact_index` is now `dict[str, dict[str, str]]`
  (was `dict[str, str]`) carrying `{"path": ..., "input_hash": ...}`
  per artifact. Old-shape state files are migrated in-memory on load
  with empty `input_hash` — safe fallback that forces reprocessing.

### Fixed

- Concurrent `state.save()` under `asyncio.gather` is now serialized by
  a module-level `asyncio.Lock`, preventing lost writes when
  `parallelism > 1`.
```

Append the new compare link to the footer:

```markdown
[1.2.0]: https://github.com/SpillwaveSolutions/docgen/compare/v1.1.0...v1.2.0
```

- [ ] **Step 2: Bump versions**

```bash
sed -i '' 's/version = "1.1.0"/version = "1.2.0"/' pyproject.toml
sed -i '' 's/__version__ = "1.1.0"/__version__ = "1.2.0"/' src/designdoc/__init__.py
sed -i '' 's/"version": "1.1.0"/"version": "1.2.0"/' plugins/designdoc/plugin.json
```

- [ ] **Step 3: Run full local CI**

Run: `task ci`
Expected: all green.

- [ ] **Step 4: Commit release**

```bash
git checkout -b chore/release-v1.2.0
git add CHANGELOG.md pyproject.toml src/designdoc/__init__.py plugins/designdoc/plugin.json uv.lock
git commit -m "chore(release): v1.2.0

Within-stage crash-resume + graceful budget halt. See CHANGELOG for
the full list.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Merge all feature branches into main**

Assuming each feature branch has been PR-reviewed and merged, `main` should contain all commits. If working as a single long-lived branch, merge it to main:

```bash
git checkout main
git merge --ff-only <branch>
git push origin main
```

- [ ] **Step 6: Tag and push v1.2.0**

```bash
git tag -a v1.2.0 HEAD -m "designdoc v1.2.0 — within-stage crash-resume + graceful budget halt

Main changes:
- Stages 2-6 checkpoint per artifact; mid-stage crash loses no prior work
- atomic_write helper + state.save() uses it
- BudgetExceededError at stage boundary: graceful halt with resume message
- artifact_index carries input_hash (dict shape, backcompat loader)

See CHANGELOG.md for full details."

git push origin v1.2.0
```

- [ ] **Step 7: Create GitHub Release**

```bash
gh release create v1.2.0 \
  --title "v1.2.0 — within-stage crash-resume + graceful budget halt" \
  --latest \
  --notes-file - <<'EOF'
<paste the v1.2.0 section from CHANGELOG.md here, with escape-free backticks>
EOF
```

---

## Self-review

(Engineer: skip this section unless you are the agent that wrote this plan.)

### Spec coverage (cross-reference against `docs/superpowers/specs/2026-04-17-within-stage-resume-design.md`)

| Spec section | Task |
|---|---|
| §1 State shape change | Task 2 |
| §1 Backcompat loader | Task 2 |
| §2 Per-artifact checkpoint flow | Tasks 4–8 |
| §3 Per-stage input_hash | Task 4 (stage 2), Task 5 (stage 3), Task 6 (stage 4), Task 7 (stage 5), Task 8 (stage 6) |
| §4 Atomic writes | Task 1 (helper) + Tasks 3–8 (use) |
| §5 Graceful budget halt | Task 9 |
| §6 Observability | Task 10 |
| §7 Testing — unit | Tasks 1, 2, 3 |
| §7 Testing — integration per stage | Tasks 4–8 |
| §7 Testing — budget halt | Task 9 |
| §7 Testing — atomic write | Task 1 |
| §7 Testing — E2E | Task 11 |

No gaps.

### Placeholder scan
- Task 6 Step 4 ("structurally identical — the engineer reads the current file") is a near-miss. Mitigation: the engineer has the exact pattern from Task 5 to mirror, and Step 3 tells them to read the file first. Keeping as-is — the pattern repetition would balloon the plan.
- Task 7 Step 2-6 compressed into "same pattern as Tasks 5 and 6" for similar reasons.
- Task 8 compressed for the same reason.

These compressions are judgment calls — the alternative would be ~400 lines of near-duplicate code, which the engineer would skim anyway. The compressed tasks explicitly reference Task 5's pattern.

### Type consistency
- `artifact_index` entries are always `{"path": str, "input_hash": str}` in every task.
- `state_lock` is the single module-level `asyncio.Lock` from Task 2.
- `atomic_write(path, content)` signature is consistent from Task 1 through Task 12.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-20-within-stage-resume-plan.md`.

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Works well for this plan because each stage task has a near-identical pattern that a fresh subagent can execute without drift.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
