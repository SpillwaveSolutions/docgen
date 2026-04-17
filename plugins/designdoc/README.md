# designdoc plugin

Claude Code slash-command wrapper for the [designdoc](../..) CLI.

## Install

```bash
cp -r plugins/designdoc ~/.claude/plugins/designdoc
```

Then in any Claude Code session:

```
/designdoc generate /path/to/your/repo
/designdoc status /path/to/your/repo
/designdoc resume /path/to/your/repo
/designdoc resolve /path/to/your/repo
```

## Prerequisites

- `designdoc` on PATH (`uv tool install .` or `pip install .` from the repo root).
- `claude` CLI logged in to a Pro/Max subscription — the SDK uses this CLI
  as its transport. No `ANTHROPIC_API_KEY` needed.
- `npx` + Node for Stage 5 mermaid validation, or use `--skip mermaid`.

## What each command does

| Command | Action |
|---|---|
| `generate` | Runs stages 0–8. Output lands in `<repo>/docs/design/`. |
| `resume` | Same code path as `generate`; the orchestrator skips DONE stages. |
| `status` | Reads `.designdoc-state.json` and `.designdoc-budget.json` and prints a summary. |
| `resolve` | Walks open HIL issues in `hil-issues.yaml` with `AskUserQuestion`, applies chosen fixes, and marks issues resolved. |

## Design rules

See [CLAUDE.md](../../CLAUDE.md) and [AGENT.md](../../AGENT.md) in the repo root for the full invariants (Gen 3 harness engineering). The short version:

1. Python enforces control flow.
2. No self-grading — checkers are isolated-context agents.
3. Retries are exactly 3, always.
4. Unresolved disputes ship with inline HIL comments + a YAML entry.
5. Mermaid gets two checkers (mmdc syntax + LLM semantic).
