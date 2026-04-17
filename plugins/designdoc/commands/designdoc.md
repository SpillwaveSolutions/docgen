---
description: Generate or resume a validated design-document tree for the current repo
argument-hint: "[generate|resume|resolve|status] [path]"
allowed-tools: Bash(designdoc:*), Read, Edit, AskUserQuestion
---

Run the designdoc CLI for: $ARGUMENTS

Subcommands:
- `generate [path]` — full pipeline (stages 0–8). Default path is cwd.
- `resume [path]` — pick up from the last checkpoint.
- `resolve [path]` — walk open HIL issues using AskUserQuestion (see below).
- `status [path]` — show pipeline state + cost ledger.

## For `generate`, `resume`, `status`

Shell out via Bash to `designdoc <subcommand> --repo <path>`. Stream the output
to the user as the pipeline runs. Common exit codes:
- `3` — mmdc preflight failed (hint: re-run with `--skip mermaid` or install Node).
- `4` — budget exceeded (hint: `designdoc status <path>` shows where we halted).

## For `resolve`

1. Read `<path>/docs/design/hil-issues.yaml`. If the file is missing or
   `unresolved_count: 0`, tell the user there's nothing to resolve and exit.
2. Pick the first issue whose `status: open`. Present its context to the user:
   - Artifact path
   - Stage name
   - `doer_said` vs `checker_said` excerpts
   - Severity and any source-file pointer
3. Call `AskUserQuestion` with the issue's `suggested_fixes` as 2–4 options
   (add a generic "Pick something else" option). Include a short free-text
   note field for clarification.
4. Apply the chosen fix by editing the affected file (`artifact` points to
   it, relative to `<path>/docs/design/`). Replace the `<!-- HIL: HIL-XXX -->`
   comment and surrounding stub text with corrected content.
5. Update the YAML entry to `status: resolved`. Recompute `unresolved_count`.
6. Report the change back to the user and offer to resolve the next open issue.

The dedicated CLI support for `resolve --emit-questions` / `resolve --apply-fix`
arrives in a later pass; until then, use Read + Edit directly from this command.
