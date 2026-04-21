# Changelog

All notable changes to **designdoc** are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-04-21

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
- **Graceful budget halt.** `BudgetExceededError` mid-stage now sets
  `state.halted_on_budget=True`, marks the stage FAILED, saves state, and
  the CLI prints `budget exhausted at $X / cap $Y. Run` `designdoc resume
  --budget <new-cap>` `to continue` with exit 0.
- **Observability.** Orchestrator stage-start log lines include the
  count of already-checkpointed artifacts (e.g., `[3/9] stage class_docs:
  40 artifacts checkpointed`).

### Changed

- `PipelineState.artifact_index` is now `dict[str, dict[str, str]]`
  (was `dict[str, str]`) carrying `{"path": ..., "input_hash": ...}`
  per artifact. Old-shape state files are migrated in-memory on load
  with empty `input_hash` — safe fallback that forces reprocessing.
- `Orchestrator.run()` no longer re-raises `BudgetExceededError`. The
  CLI reads `state.halted_on_budget` after the run and formats the
  resume message itself. Exit code 0 (resumable), not 4 (crash).

### Fixed

- Concurrent `state.save()` under `asyncio.gather` is now serialized by
  a module-level `asyncio.Lock`, preventing lost writes when
  `parallelism > 1`.

## [1.1.0] - 2026-04-17

Incremental regeneration, parallel execution, and UX polish. Measured on
`tests/fixtures/tiny_repo`: cold runs dropped from **~26 min → ~16 min** (-37%)
and warm runs (no source changes) dropped from a full regen to **<1 s / $0.00**.

### Added

- **Incremental regeneration.** Every generated artifact records the SHA1 of
  its inputs in `.designdoc-state.json`. Subsequent runs skip unchanged work:
  - Stage 2/3 skip files whose source hash matches the previous run.
  - Stage 4/5/7 skip rollups whose input-class-doc hashes are unchanged.
  - Stage 6 uses a tech-debt manifest hash to skip cross-ref research.
- **Parallel per-artifact execution.** Stages 2, 3, 4, and 6 now run their
  per-file / per-class / per-package / per-topic loops under an
  `asyncio.Semaphore(parallelism)` gate. Default parallelism is 3.
- **`--parallelism N` CLI flag** on `designdoc generate` and `designdoc resume`
  (overrides `config.parallelism`).
- **Progress logging.** Orchestrator emits `[N/9] stage x starting` and
  `[N/9] stage x done in Xs` lines so long runs are observable.
- **`designdoc status` cache-readiness hints.** Reports `prev_hashes` and
  `rollup_hashes` counts so users can see what will skip on the next run.
- **Incremental-regeneration benchmark** (`tests/e2e/test_incremental_bench.py`)
  — real-API cold/warm run against `tiny_repo`; gated on `claude` CLI login.

### Changed

- README documents measured parallel speedup and incremental behavior.
- Config fields `include_languages`, `output_dir`, and `diagram_format` are now
  wired through to the pipeline (previously declared but unused).
- CI workflow runs on `feat/**`, `fix/**`, `bench/**`, `chore/**`, `docs/**`,
  and `test/**` branches (broader than the original `feat/**`-only gate).

### Fixed

- `--config` path and MCP server list are now passed through to every stage;
  previously dropped after CLI parsing.
- Mermaid double-append bug in the Stage 5 rollup path (manifested when a
  rollup was regenerated without clearing its prior output).
- CI `mmdc` invocation runs with `--no-sandbox` so Puppeteer works on GitHub
  Actions runners.
- Three ultrareview findings on option passthrough (error surface, default
  propagation, and test isolation).

### Docs

- Authentication uses the local `claude` CLI (Pro/Max subscription) as SDK
  transport. No `ANTHROPIC_API_KEY` required for normal use.

## [1.0.0] - 2026-04-16

First feature-complete release. Implements the 9-stage harness-engineered
documentation pipeline described in `plans/2026_04_16_designdoc_gen_v1.md`.

### Added

#### Core harness (the invariants)

- **`loop.py`** — 3-attempt doer/checker loop (`MAX_ATTEMPTS = 3`), the
  canonical retry cap. Constitutional guard test
  (`test_config_does_not_expose_max_attempts`) prevents the constant from ever
  being surfaced as user config.
- **`verdict.py`** — Pydantic `CheckerVerdict` schema with anti-self-grading
  validator; malformed JSON produces a synthetic `fail` verdict rather than
  silently succeeding.
- **`budget.py`** — `CostAccumulator` with hard cap; raises
  `BudgetExceededError` when exceeded. Persisted to state so resumed runs
  honor the remaining budget.
- **`state.py`** — resumable pipeline state machine. Each completed stage
  writes a checkpoint to `<output>/.designdoc-state.json`; a crashed run
  resumes from the last completed stage.
- **`runner.py`** — centralized `ClaudeSDKRunner` that accrues cost for every
  LLM call through a single chokepoint.
- **`hil.py`** — Human-in-the-loop issue model, YAML emit, and inline
  `<!-- HIL: HIL-XXX -->` comment helper. Unresolved disputes ship with the
  doc rather than blocking the pipeline.
- **`config.py`** — TOML config loader.

#### Nine pipeline stages

- **Stage 0** — Language discovery, file tree, per-language manifest (no LLM).
- **Stage 1** — AST-lite signature extraction. Python uses `ast`; TS/JS falls
  back to regex for v1.
- **Stage 2** — Per-file summary via `doer_schema_loop` (LLM doer + Pydantic
  schema checker).
- **Stage 3** — Class documentation via `class-documenter` doer +
  `doc-quality-checker` LLM checker (first LLM-on-LLM stage).
- **Stage 4** — Package README rollups from class docs.
- **Stage 5** — Mermaid diagrams via two-checker loop: deterministic
  `mmdc` syntax check **plus** an LLM semantic check. Both must pass.
- **Stage 6** — Tech-debt researcher with Perplexity / Context7 MCP
  cross-referencing.
- **Stage 7** — `SYSTEM_DESIGN.md` + `ARCHITECTURE.md` rollup from package
  READMEs.
- **Stage 8** — Finalize: README TOC assembly and `hil-issues.yaml` emit.

#### CLI and plugin

- **Typer CLI** with `generate`, `resume`, `status`, and `resolve`
  subcommands.
- **`/designdoc` Claude Code slash command** wrapper and plugin glue.
- **HIL walker CLI helpers** for resolving unresolved disputes post-run.

### Infrastructure

- Taskfile + `task ci` as the canonical test command. GitHub Actions mirrors
  it exactly (CI-parity discipline).
- End-to-end test against `tests/fixtures/tiny_repo` using the real Claude
  CLI; skipped automatically when the CLI isn't logged in.
- `mmdc` preflight probe at orchestrator start; pipeline halts with a clear
  error if the Mermaid CLI is absent.

[1.2.0]: https://github.com/SpillwaveSolutions/docgen/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/SpillwaveSolutions/docgen/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/SpillwaveSolutions/docgen/releases/tag/v1.0.0
