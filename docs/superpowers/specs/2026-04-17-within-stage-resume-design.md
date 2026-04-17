# v1.2 — Within-Stage Crash-Resume

**Status:** Approved
**Date:** 2026-04-17
**Author:** Rick Hightower (with Claude Opus 4.7)
**Target milestone:** v1.2.0

## Problem statement

v1.1's incremental regeneration (`prev_hashes`) reduces *cross-run* rework to
zero when nothing has changed, and to a single-file scope when one file
changed. What it does not help with is *within-run* loss: if a cold run
crashes mid-Stage-3 after generating 40 of 60 class docs, the next invocation
re-processes all 60 classes from scratch. For a large repo where a cold
run is 16 minutes today, that is 16 minutes of LLM work thrown away on every
crash.

Three scenarios drive the feature:

- **A. Process kill mid-stage.** Ctrl-C, OOM, laptop lid close, SIGKILL.
- **B. Budget cap hit mid-stage.** User bumps the cap and wants to resume
  where work halted, not restart.
- **C. Stage 5 (mermaid) flakes in batches.** `mmdc` or an LLM syntax-check
  can fail on one class; a retry of that class alone should suffice.

All three share the same underlying mechanic: every artifact a stage
produces should survive process death, and every *already produced*
artifact should be skippable on the next invocation.

## Goals

- After any mid-stage process death, `designdoc resume` continues from the
  next un-produced artifact in the same stage. No LLM call is wasted redoing
  completed work.
- After a budget-cap halt, the pipeline exits cleanly (exit 0) with a pointer
  to the exact resume command. `designdoc resume --budget <new-cap>` bumps
  the cap and continues.
- Crashes during artifact write do not leave truncated files on disk.
- Backwards-compatible with existing `.designdoc-state.json` files — old
  state loads and cleanly forces reprocessing rather than erroring.

## Non-goals

- **No change to the doer/checker 3-attempt cap.** Within-stage resume
  checkpoints *final* artifacts (a doc that passed or shipped-with-HIL).
  Intermediate loop retries remain in-memory; they do not persist.
- **No change to `prev_hashes` semantics.** That is v1.1's cross-run skip
  logic. It remains seeded from `artifact_index` at Stage 8 finalize,
  exactly as today.
- **No Stage 7 resume.** Stage 7 produces two artifacts (`SYSTEM_DESIGN.md`
  and `ARCHITECTURE.md`); a crash there loses at most two LLM calls.
  Checkpointing would add complexity for negligible benefit.
- **No tree-sitter for TS/JS, no PlantUML, no Agent Brain MCP.** Those are
  separately tracked v1.x items.

## Architecture

### State shape change

`PipelineState.artifact_index` type changes from `dict[str, str]` to
`dict[str, dict[str, str]]`. Each entry carries both the output path and
the hash of the inputs that produced it.

```python
# src/designdoc/state.py
artifact_index: dict[str, dict[str, str]] = field(default_factory=dict)
#   Example entry:
#     "src/foo.py::Bar": {
#         "path":       "packages/foo/classes/Bar.md",
#         "input_hash": "a1b2c3..."
#     }
```

`load_or_new` includes a backcompat shim: any string-typed value is
migrated in-memory to `{"path": value, "input_hash": ""}`. Empty-hash
entries never match any current hash, so old state cleanly forces
reprocessing without data loss.

### Per-artifact checkpoint flow

Every stage's per-artifact coroutine follows the same pattern:

```python
async def process_one(artifact_id, current_input_hash, ...):
    # Skip check — pre-existing completion survives process death.
    prior = state.artifact_index.get(artifact_id, {})
    if prior.get("input_hash") == current_input_hash and out_path.exists():
        return prior["path"]

    # Normal doer/checker loop — unchanged.
    result = await doer_checker_loop(...)

    # Atomic write.
    atomic_write(out_path, result.text)

    # Checkpoint under lock (asyncio.gather may run multiple in parallel).
    async with state_lock:
        state.artifact_index[artifact_id] = {
            "path": rel,
            "input_hash": current_input_hash,
        }
        state.save()
    return rel
```

`state_lock` is a module-level `asyncio.Lock()` in `state.py`, acquired
only around the save. Concurrent gather-children serialize their JSON
rewrites but not their LLM calls.

### Atomic writes

A new helper `atomic_write(path, content)` in `src/designdoc/io_utils.py`:

```python
def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)   # POSIX-atomic rename
```

Every stage that writes artifact files (2, 3, 4, 5, 6, 7, 8) switches to
this helper. Guarantees that a SIGKILL mid-write never leaves a truncated
artifact on disk: either the `.tmp` file is partial (ignored on next run),
or the final file is complete.

### Per-stage `input_hash` composition

| Stage | `input_hash` is SHA1 of |
|---|---|
| 2 — file analysis | source file contents (reuse Stage 0 hash) |
| 3 — class docs | `source_sha + json.dumps(class_signature, sort_keys=True)` |
| 4 — package rollups | concat of sorted class-doc input hashes in that package |
| 5 — mermaid | the class-doc input hash (one diagram per class doc) |
| 6 — tech debt | the dep-manifest-entry SHA for that topic |

Stages 4 and 6 already compute hashes of this shape via `rollup_hashes`.
The new work is extending the pattern to per-artifact grain in Stages 2,
3, and 5 (per-class diagrams). Stage 7 is deliberately out of scope — see
non-goals above.

### Graceful budget halt

Orchestrator wraps each stage in a `BudgetExceededError` handler:

```python
try:
    await stage.run(...)
except BudgetExceededError as e:
    state.save()
    typer.echo(
        f"Budget exhausted mid-stage {stage_name} at "
        f"${e.spent:.2f} / cap ${e.cap:.2f}."
    )
    typer.echo(
        f"Run `designdoc resume --budget <new-cap>` to continue."
    )
    sys.exit(0)
```

`designdoc resume` gains a `--budget FLOAT` flag that overrides the cap in
state before the pipeline resumes. All other CLI surface is unchanged.

### Observability on resume

The existing `[N/9] stage x starting` / `done in Xs` log lines gain a
checkpoint-count annotation:

```
[3/9] class_docs: 40/60 artifacts checkpointed, processing 20 remaining
[3/9] class_docs done in 4m 12s
```

When all artifacts in a stage are already checkpointed (pure v1.1
incremental case, no new work):

```
[3/9] class_docs: all 60 checkpointed, skipped
```

## Data flow

```
┌──────────────────┐
│ designdoc resume │
└────────┬─────────┘
         │
         ▼
┌──────────────────────────────────────────────────────┐
│ Orchestrator: load state, find first RUNNING stage   │
└────────┬─────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────┐
│ Stage 3 (class_docs) run()                           │
│                                                      │
│ for each class:                                      │
│   compute current_input_hash                         │
│   if state.artifact_index[id].input_hash == current  │
│      and out_path.exists():                          │
│       skip (log: checkpointed, skipping)             │
│   else:                                              │
│       doer/checker loop                              │
│       atomic_write(out_path, result)                 │
│       async with state_lock:                         │
│           state.artifact_index[id] = {path, hash}    │
│           state.save()                               │
│                                                      │
│ mark stage DONE, save()                              │
└──────────────────────────────────────────────────────┘
```

On SIGKILL: any artifact whose `state.save()` completed before the kill
survives; its entry in `artifact_index` persists; on resume it is skipped.
Any artifact whose LLM call was in flight when the kill landed has no
state entry, so on resume it regenerates.

## Backwards compatibility

- **Old `.designdoc-state.json` files** (with string-valued
  `artifact_index`): backcompat loader auto-migrates to dict shape with
  empty `input_hash`. Every artifact reprocesses on next run — same
  behavior as today, no data loss.
- **v1.1 `prev_hashes` semantics**: unchanged. Still seeded by Stage 8
  finalize from `artifact_index` hashes.
- **Existing CLI flags**: unchanged. `--budget` on `resume` is additive.
- **Existing tests**: none should fail. The guard test
  `test_config_does_not_expose_max_attempts` still holds — we are not
  introducing any retry-count config.

## Testing strategy

### Unit

- `state.py`: round-trip with new shape; backcompat loader migrates
  string-valued entries with empty `input_hash`; round-trip preserves
  the dict shape.
- `io_utils.atomic_write`: creates `.tmp`, renames via `os.replace`,
  raises if parent doesn't exist, leaves no `.tmp` behind on success.

### Integration (per stage, 2/3/4/5/6)

- **Mid-stage kill simulation.** Patch the per-artifact coroutine to
  raise after N successful artifacts. Run the stage; assert
  `.designdoc-state.json` shows N entries in `artifact_index`. Re-invoke
  the stage with the patch removed; assert only `(total - N)` LLM calls
  fire.
- **All-checkpointed skip.** Pre-populate `artifact_index` for every
  artifact in the stage with hashes matching current inputs. Run the
  stage; assert zero LLM calls.

### Integration — budget halt

- Set a low cap (e.g., $0.50). Run generate with stub LLM that always
  costs $0.10. Pipeline should halt mid-Stage-3 with exit 0 and the
  resume-message format. `.designdoc-state.json` should show partial
  `artifact_index`. Invoke `designdoc resume --budget 5.00`; verify it
  continues and completes without re-calling any checkpointed artifact.

### Integration — atomic write

- Simulate SIGKILL between `tmp.write_text` and `os.replace`: assert
  `out_path` does not exist, re-run regenerates cleanly.
- Simulate SIGKILL after `os.replace`: assert `out_path` is complete and
  resume skips it.

### E2E (on `tests/fixtures/tiny_repo`)

- Dogfood-style run with a pytest monkeypatch that raises mid-Stage-3
  after 2 class docs succeed. Run `designdoc resume`. Verify final doc
  tree is byte-identical to a clean cold run. Marked `requires_api`
  (gated on `claude` CLI login), same policy as existing E2E tests.

## Risk and mitigation

- **Risk:** `state.save()` race on concurrent gather-children. 
  **Mitigation:** `asyncio.Lock` in `state.py`, acquired only around
  the save. Lock contention is negligible at parallelism ≤ 4.
- **Risk:** `os.replace` is not atomic on Windows across different
  drives. 
  **Mitigation:** our output tree lives under one `output_dir`, so
  source and destination are always on the same filesystem. Document
  the assumption in a docstring comment on `atomic_write`.
- **Risk:** a corrupted `.designdoc-state.json` from a partial write.
  **Mitigation:** apply `atomic_write` to the state file itself, not just
  artifacts. Same 3-line change.
- **Risk:** user expects `--budget` on `resume` to persist for subsequent
  resumes.
  **Mitigation:** `--budget` updates `state.budget_cap`, which persists
  across resumes via the state file — so it sticks until overridden
  again.

## Rollout

v1.2.0 ships this feature. No migration script required — the
backcompat loader handles existing state files. CHANGELOG.md gains a
v1.2.0 entry; no breaking changes.

## Open questions

None at design time. All shaping decisions locked in during brainstorming:

- ✅ Full scope — scenarios A, B, and C all in.
- ✅ State shape: extend `artifact_index` in-place (option A from
  brainstorming, not append-only log).
- ✅ Auto-resume silently; no new opt-in flag required.
- ✅ Graceful budget halt with resume-message format.
