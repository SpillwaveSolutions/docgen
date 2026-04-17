# designdoc

Harness-engineered codebase documentation pipeline.

Walks any repo bottom-up and emits a validated `docs/design/` tree: per-class docs, package rollups, mermaid diagrams (syntax + semantics validated), a system-design rollup, a tech-debt ledger, and a YAML file of unresolved human-in-the-loop disputes.

## Quickstart

```bash
uv sync
uv run designdoc generate --repo /path/to/your/repo --budget 5.00
```

Output lands in `<repo>/docs/design/`.

## Design principles (Gen 3 harness engineering)

1. Control flow lives in Python, not prompts.
2. Checkers run in their own context window (no self-grading).
3. Scopes are small and bounded (file → class → package → system).
4. Failures are loud (schema-validated verdicts, HIL YAML on dispute).
5. Reliability over speed (`max_attempts=3`, bounded parallelism).
6. Mermaid is syntax + semantics validated before shipping.

See `CLAUDE.md` for the full invariants and `plans/` for implementation plans.

## Development

```bash
task test          # unit + integration, no API
task test-e2e      # requires ANTHROPIC_API_KEY
task lint
task format
task dogfood       # real API run against tests/fixtures/tiny_repo
```

## Claude Code plugin

A `/designdoc` slash command wrapper lives in `plugins/designdoc/`. Install with:

```bash
cp -r plugins/designdoc ~/.claude/plugins/designdoc
```

Commands: `/designdoc generate | resume | status | resolve`.
