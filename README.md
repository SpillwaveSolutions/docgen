# designdoc

Harness-engineered codebase documentation pipeline.

Walks any repo bottom-up and emits a validated `docs/design/` tree: per-class docs, package rollups, mermaid diagrams (syntax + semantics validated), a system-design rollup, a tech-debt ledger, and a YAML file of unresolved human-in-the-loop disputes.

## Quickstart

```bash
uv sync
uv run designdoc generate --repo /path/to/your/repo --budget 5.00
```

Output lands in `<repo>/docs/design/`.

## Status

**v1 complete, v1.1 incremental-regeneration landed.** All nine pipeline stages are implemented, tested, and measured end-to-end. See `plans/2026_04_16_designdoc_gen_v1.md` for the task plan.

### Measured performance

Against the `tests/fixtures/tiny_repo` fixture (5 Python files, 3 classes, 1 dep) on `claude-sonnet-4-6` via a Claude Max subscription:

| Run | Wall clock | Cost (SDK-reported) | LLM invocations |
|---|---|---|---|
| **Cold** (first run) | ~26 min | ~$4.57 | 60 |
| **Warm** (no source changes) | < 1 sec | $0.00 | 0 |

The warm run skips every stage via content-hash comparison against `prev_hashes` / `rollup_hashes` in the pipeline state. Any single-file edit regenerates only that file's class doc + its package rollup + the system rollup — not the whole tree.

Reproduce with `task test-e2e` (requires `claude` CLI logged in and `npx` on PATH).

## Design principles (Gen 3 harness engineering)

1. Control flow lives in Python, not prompts.
2. Checkers run in their own context window (no self-grading).
3. Scopes are small and bounded (file → class → package → system).
4. Failures are loud (schema-validated verdicts, HIL YAML on dispute).
5. Reliability over speed (`max_attempts=3`, bounded parallelism).
6. Mermaid is syntax + semantics validated before shipping.

See `CLAUDE.md` / `AGENT.md` for the full invariants.

## Development

### Prerequisites

- Python 3.12+ (dev machine runs 3.13)
- [uv](https://github.com/astral-sh/uv) for env management
- [Task](https://taskfile.dev/) for running commands
- `@mermaid-js/mermaid-cli` via `npx` (auto-fetched at Stage 5 preflight)
- `claude` CLI (Claude Code) logged in to a Pro/Max subscription — used by
  the e2e / dogfood runs. No `ANTHROPIC_API_KEY` required.

### Commands

```bash
task install         # uv sync — install deps
task test            # unit + integration, no real API
task test-unit       # unit tests only
task test-e2e        # e2e tests (requires API key + mmdc)
task lint            # ruff check
task format          # ruff format
task ci              # exactly what CI runs — must be green before push
task dogfood         # real pipeline run against tests/fixtures/tiny_repo
```

Run a single test:

```bash
uv run pytest tests/unit/test_loop.py::test_ships_with_hil_after_3_fails -v
```

### Test-and-commit discipline

Every change follows **TWRC**: write the test, write the code, run `task ci`, commit.

**CI parity:** `task ci` must run the exact same commands as `.github/workflows/test.yml`. If you change one, change the other in the same commit. Every commit is a green checkpoint.

### Layout

```
src/designdoc/
  loop.py          # the invariant — 3-attempt doer/checker bouncer
  verdict.py       # pydantic schemas with anti-self-grading validator
  budget.py        # cost accumulator with hard cap
  state.py         # resumable pipeline state
  hil.py           # human-in-the-loop issue YAML
  runner.py        # centralized Claude SDK wrapper
  config.py        # TOML config loader
  agents/          # per-agent system prompts + AgentDef factories
  stages/          # s0_discover..s8_finalize
  mermaid/         # mmdc wrapper + two-checker loop
  index/           # discover + AST-lite signatures
  templates/       # Jinja2 templates for generated docs
plugins/designdoc/ # Claude Code slash command
tests/{unit,integration,e2e,fixtures}/
plans/             # implementation plan
```

## Claude Code plugin

A `/designdoc` slash command wrapper lives in `plugins/designdoc/` (to be built in Task 21):

```bash
cp -r plugins/designdoc ~/.claude/plugins/designdoc
```

Commands: `/designdoc generate | resume | status | resolve`.

## License

MIT.
