# ULTRAREVIEW follow-up — 2026-04-22

## Context

On 2026-04-22 a whole-project review of `designdoc` was run with 5 parallel
reviewers (invariants, architecture, code quality, tests, silent failures).
The review surfaced ~20 findings across 5 tiers; this plan ticketizes them so
GitHub Issues remains the canonical work ledger (per CLAUDE.md PR Workflow).

Two reviewer claims were verified-and-rejected before ticketization (see
"False positives caught" appendix). The 13 tickets below are net of those.

**Closure criterion:** every row's issue closes via a merged PR. When all 13
close, this plan file is deleted (or moved under `plans/archive/`) and the
"Active Plan" pointer is removed from CLAUDE.md / AGENT.md / CONTRIBUTING.md.

---

## Tickets (13 total)

Issue # column is back-filled in a separate PR after issue creation.

### Tier 1 — Confirmed bugs (5 tickets, `bug` template)

| # | Issue | Title | Evidence | Suggested branch |
|---|---|---|---|---|
| 1 | [#6](https://github.com/SpillwaveSolutions/docgen/issues/6) | `fix: asyncio.gather lets sibling tasks burn budget after BudgetExceededError` | `s2_file_analysis.py:111`, `s3_class_docs.py:136`, `s6_tech_debt.py:126` | `fix/budget-leak-under-parallelism` |
| 2 | [#7](https://github.com/SpillwaveSolutions/docgen/issues/7) | `fix: split total_retries metric into doer-content vs checker-parse retries` | `state.py:33`, `cli.py:223`; refs INV-001 action item | `fix/inv-001-total-retries-metric` |
| 3 | [#8](https://github.com/SpillwaveSolutions/docgen/issues/8) | `fix: use atomic_write in s7_system_rollup, s0_discover, s1_index` | `s7_system_rollup.py:92-93`, `s0_discover.py:35`, `s1_index.py:43` | `fix/atomic-writes-stages-0-1-7` |
| 4 | [#9](https://github.com/SpillwaveSolutions/docgen/issues/9) | `fix: add within-stage checkpoint to stage 7 (or document carve-out)` | `s7_system_rollup.py:55` (no `artifact_index`) | `fix/stage7-within-stage-resume` |
| 5 | [#10](https://github.com/SpillwaveSolutions/docgen/issues/10) | `chore: enforce state_lock around all state.save() call sites` | `orchestrator.py:134`, `s0_discover.py:35`, `s1_index.py:26`, `s7_system_rollup.py:46`, `s8_finalize.py:28,42` | `chore/serialize-state-saves` |

### Tier 2 — High-value refactors (5 tickets, `enhancement` template)

| # | Issue | Title | Evidence | Suggested branch |
|---|---|---|---|---|
| 6 | [#11](https://github.com/SpillwaveSolutions/docgen/issues/11) | `refactor: extract sha1_keyed helper from 4 stage hash duplications` | `s4:137`, `s7:109`, `s6:140`, `s6:153` | `refactor/io-utils-sha1-keyed` |
| 7 | [#12](https://github.com/SpillwaveSolutions/docgen/issues/12) | `refactor: deduplicate _current_source_hashes between s2 and s3` | `s2:127` ↔ `s3:156` (literal copy-paste) | `refactor/dedupe-source-hashes` |
| 8 | [#13](https://github.com/SpillwaveSolutions/docgen/issues/13) | `refactor: move per-stage kwargs and id-prefix into StageEntry` | `orchestrator.py:159` `_id_belongs_to_stage`, `:173` `_stage_kwargs` | `refactor/stage-entry-self-describing` |
| 9 | [#14](https://github.com/SpillwaveSolutions/docgen/issues/14) | `refactor: extract _ship_with_hil shared by doer_checker_loop and doer_schema_loop` | `loop.py:98-115` ↔ `loop.py:264-268` | `refactor/loop-ship-with-hil-helper` |
| 10 | [#15](https://github.com/SpillwaveSolutions/docgen/issues/15) | `refactor: route mermaid checker output through MermaidIssue (or delete the class)` | `verdict.py:35` defined; `mermaid/loop.py:60-69` emits plain `CheckerIssue` | `refactor/mermaid-issue-or-delete` |

### Tier 3-5 — Batched cleanup (3 tickets, `enhancement` template, checklists)

| # | Issue | Title | Sub-items |
|---|---|---|---|
| 11 | [#16](https://github.com/SpillwaveSolutions/docgen/issues/16) | `chore: polish pass — dead exports, naming, type hints` | T3.1 drop dead `noqa: F401` for `AgentDef` re-export (`loop.py:30`) · T3.2 rename `_SDKProtocol` → `SDKProtocol` (`runner.py:38`) · T3.3 add `RunnerProtocol(Protocol)` to replace `runner: Any` (`loop.py:26`) · T3.4 collapse dead `isinstance(e, ValidationError)` branch under `except Exception` (`cli.py:140-153`) · T3.5 promote `_CompositeCheckerRunner` to module-level dataclass (`mermaid/loop.py:78`) · T3.6 collapse default-duplication in `load_config` (`config.py:60-75`) |
| 12 | [#17](https://github.com/SpillwaveSolutions/docgen/issues/17) | `test: add guard tests for invariants and fragile glue` | T4.1 type-level guard for invariant 2 (no self-grading) — assert checker prompt never contains prior-attempt content · T4.2 unit test for `_id_belongs_to_stage` classifier (`orchestrator.py:159`) · T4.3 assertion in `test_runner_mcp.py` that `setting_sources` + `mcp__<server>__*` glob get added when `mcp_servers` is non-empty (`runner.py:78-81`) · T4.4 add missing `test_stage8_resume.py` |
| 13 | [#18](https://github.com/SpillwaveSolutions/docgen/issues/18) | `chore: silent-failure cleanup` | T5.1 narrow bare `except Exception:` in `_parse_or_placeholder` (`s2_file_analysis.py:154-164`) and log unexpecteds · T5.2 add path-traversal assertion in `_class_doc_path` (`s3_class_docs.py:176-183`) · T5.3 log `JSONDecodeError` in `_parse_report` (`s6_tech_debt.py:165-178`) · T5.4 use `atomic_write` in `hil.py:78` and `resolve.py:37` |

---

## Status tracking

| Issue | Status | Closed by |
|---|---|---|
| [#6](https://github.com/SpillwaveSolutions/docgen/issues/6)   | closed | [PR #31](https://github.com/SpillwaveSolutions/docgen/pull/31) |
| [#7](https://github.com/SpillwaveSolutions/docgen/issues/7)   | closed | [PR #30](https://github.com/SpillwaveSolutions/docgen/pull/30) + [PR #32](https://github.com/SpillwaveSolutions/docgen/pull/32) (wiring follow-up) |
| [#8](https://github.com/SpillwaveSolutions/docgen/issues/8)   | closed | [PR #21](https://github.com/SpillwaveSolutions/docgen/pull/21) |
| [#9](https://github.com/SpillwaveSolutions/docgen/issues/9)   | closed | [PR #29](https://github.com/SpillwaveSolutions/docgen/pull/29) |
| [#10](https://github.com/SpillwaveSolutions/docgen/issues/10) | closed | [PR #23](https://github.com/SpillwaveSolutions/docgen/pull/23) |
| [#11](https://github.com/SpillwaveSolutions/docgen/issues/11) | closed | [PR #27](https://github.com/SpillwaveSolutions/docgen/pull/27) |
| [#12](https://github.com/SpillwaveSolutions/docgen/issues/12) | closed | [PR #25](https://github.com/SpillwaveSolutions/docgen/pull/25) |
| [#13](https://github.com/SpillwaveSolutions/docgen/issues/13) | open   | — |
| [#14](https://github.com/SpillwaveSolutions/docgen/issues/14) | closed | [PR #26](https://github.com/SpillwaveSolutions/docgen/pull/26) |
| [#15](https://github.com/SpillwaveSolutions/docgen/issues/15) | open   | — |
| [#16](https://github.com/SpillwaveSolutions/docgen/issues/16) | closed | [PR #22](https://github.com/SpillwaveSolutions/docgen/pull/22) |
| [#17](https://github.com/SpillwaveSolutions/docgen/issues/17) | open   | — |
| [#18](https://github.com/SpillwaveSolutions/docgen/issues/18) | open   | — |

When a PR lands and closes its issue, replace `open` with `closed` and fill
in the PR number. When all 13 are closed, delete this file and the related
"Active Plan" pointers.

---

## Appendix A — False positives caught during synthesis

Two reviewer claims were verified directly against the code and rejected.
Recording them here so future readers do not re-investigate.

1. **"`runner.py` silently drops the configured `model`"** — *FALSE.*
   `src/designdoc/runner.py:85` populates `model` from `agent.model` in
   `_build_options`, and `runner.py:112` passes that value into
   `ClaudeAgentOptions`. The configured `doer_model` / `checker_model` reach
   the SDK as intended.

2. **"`hil.inline_comment()` is dead code"** — *FALSE.*
   `inline_comment` is imported and called by **four** stages:
   `s3_class_docs.py:33,123`, `s4_package_rollups.py:25,99`,
   `s5_mermaid.py:24,87`, `s7_system_rollup.py:19,88`. Invariant 5 (HIL
   fallback ships with inline marker) is **fully implemented**, not
   half-implemented as the invariant agent claimed.

---

## Appendix B — What the project gets right (do-not-touch)

These looked like potential review targets but the existing design is
correct on its own terms. Do not "fix" them without first revisiting the
rationale.

- **`MAX_ATTEMPTS = 3` as a module constant, not a config field**
  (`loop.py:38`). Looks like an obvious "make configurable" candidate;
  CLAUDE.md §3 + `tests/unit/test_config.py:61-71` make it constitutional.
- **`mermaid/loop.py` proxy-runner trick** (lines 78–98). Feels over-clever,
  but it is the cheapest way to keep syntax-then-semantic ordering enforced
  outside a prompt while reusing `doer_checker_loop`'s 3-attempt cap and HIL
  bookkeeping. Refactoring would duplicate Invariant 6 logic.
- **`verdict.parse_verdict`** — non-destructive code-fence strip, explicit
  four-exception catch, loud synthetic-fail with raw output preserved. The
  recent INV-001 fix completed this well.
- **`io_utils.atomic_write`** — `.tmp`-then-`os.replace` is atomic on POSIX.
  Three stages skip it (ticketed as #3 above), but the helper itself is
  correct and is the right pattern.
- **Subprocess invocations** (`mmdc.py:41`, `:86`) — list-form argv, no
  `shell=True`, target-codebase paths never reach the shell.
