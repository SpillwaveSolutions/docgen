# Contributing to designdoc

This document is the canonical PR and issue workflow for this repo. AI-assistant
context files (`CLAUDE.md`, `AGENT.md`, etc.) mirror this policy locally, but
**this file is the source of truth** when the two diverge.

## Non-negotiable invariants

The system's correctness claim rests on a set of Gen-3 harness-engineering
invariants. See `CLAUDE.md` / `AGENT.md` for the full list. Any PR that affects
code in `src/designdoc/loop.py`, `src/designdoc/verdict.py`, or
`src/designdoc/mermaid/loop.py` **must** explicitly confirm in the PR
description which invariants are touched and how they are preserved.

Core invariants (abridged):

1. **Control flow in Python, not prompts.** `for attempt in range(1, MAX_ATTEMPTS + 1)` stays in `loop.py`.
2. **No self-grading.** Checker sees only: source artifact + doer's final output + its own system prompt.
3. **`MAX_ATTEMPTS = 3`.** Not configurable. A guard test enforces this at the type level.
4. **Schema-validated verdicts, fail loud.** Malformed JSON → synthetic `fail`. Do not silently repair content.
5. **HIL fallback.** After 3 failed attempts, ship the doc with `<!-- HIL: HIL-NNN -->` and append to `hil-issues.yaml`.
6. **Mermaid two-checker.** `mmdc` + LLM. Never let an LLM-only validation path slip in.

## Test-and-Commit Discipline (TWRC)

Every change follows **Test → Write → Run → Commit**:

1. Write the failing test first (unit + integration + E2E if applicable).
2. Write code that makes the test pass.
3. Run the test. A change is not done until `task ci` passes locally.
4. Tasks touching external boundaries (CLI, full pipeline, generated artifacts)
   require an E2E or integration test. Tests requiring the real API live under
   `tests/e2e/` with `@pytest.mark.requires_api`.
5. Commit only after the test run succeeds. Every commit is a green checkpoint.

### CI-parity rule

**Whatever the GitHub Actions gate runs must run locally via `task ci`.** If
you change one, change the other in the same commit. See
`.github/workflows/test.yml` and `Taskfile.yml:ci` — they stay in lockstep.

```bash
task ci   # runs lint + format-check + unit + integration; must be green before push
```

## PR Workflow (MANDATORY)

**No direct commits to `main`.** Every change ships via a pull request. `main`
is a record of merged PRs, not in-progress work.

### Branch naming

| Prefix | Use for |
|---|---|
| `fix/<scope>-<desc>` | Bug fixes. For investigations, use `fix/inv-NNN-<desc>`. |
| `feat/<scope>-<desc>` | New features. Reference the plan spec under `plans/` if one exists. |
| `chore/<desc>` | Tooling, deps, CI, workflow, non-code docs. |
| `docs/<scope>` | Code-adjacent documentation changes (README, CONTRIBUTING). |
| `test/<scope>` | Test-only changes. |
| `bench/<scope>` | Benchmarking / measurement experiments not intended to ship. |

### The flow

1. **Find or open a GitHub Issue first.** Trivial typo fixes can skip; anything
   else needs one.
   - Bugs / investigations use the `INV-NNN` title convention. The full analysis
     lives in `plans/future_improvements_investigations.md` with evidence under
     `plans/investigations/INV-NNN/`.
   - Features reference their plan spec under `plans/`.
2. **Create a feature branch from `main`.** One logical change per PR.
3. **Work follows TWRC.** Each commit is a green checkpoint.
4. **`task ci` green locally** before pushing.
5. **Open the PR** using `.github/PULL_REQUEST_TEMPLATE.md`. The description must:
   - Reference the issue (`Closes #N` / `Part of #N`).
   - Explain the *why*, not just the *what*.
   - Cite invariants touched and confirm preservation.
6. **All CI checks must pass before merge.** No admin override, no `--no-verify`.
7. **Merge via rebase-and-merge** — preserves linear history, matches the v1.2.0 precedent.
8. **Clean up**: `git branch -D <name>` locally, `git checkout main && git pull --prune`.

### What NOT to do

- Never force-push to `main` or any branch with an open PR.
- Never merge your own review-required PR (pure-chore PRs you explicitly label as such are fine).
- Never bypass failing CI.
- Never commit directly to `main`, even for "trivial" changes.
- Never use `git commit --amend` on commits that have been pushed.

## GitHub Issues = the work ledger

- **Every non-trivial PR closes or references an issue.** No issue = not started.
- **Investigation issues use the `INV-NNN` title prefix.** Body summarizes;
  `plans/future_improvements_investigations.md` holds the analysis.
- **Closing criteria must be explicit in the issue.** "Looks better" is not
  closeable; "task ci green on test X" is.
- **Labels** follow `.github/ISSUE_TEMPLATE/`: `bug`, `enhancement`, `investigation`.

## Specs live under `./plans/`

| Path | Contents |
|---|---|
| `plans/<YYYY>_<MM>_<DD>_<name>.md` | Milestone / roadmap plans. |
| `plans/future_improvements_investigations.md` | Ledger of open and resolved investigations (INV-NNN). |
| `plans/investigations/INV-NNN/` | Per-investigation evidence bundles: raw captures, reviewer second opinions, context packets. |

Plan files are the source of truth for scope. PRs cite them; issues reference
them. Do not duplicate plan content into issues — link to the plan instead.

## Second opinions for load-bearing analytical claims

When a PR hinges on an analytical claim about system behavior (root cause,
"healthy pattern," correctness argument) that isn't obvious from the code,
validate the claim with an external reviewer before shipping. Tools that work:

```bash
# Gemini 2.5 Pro
cat context.md | gemini "<prompt>" --model gemini-2.5-pro > gemini_review.md

# Codex CLI (read-only sandbox)
cat context.md | codex exec -s read-only "<prompt>" > codex_review.md
```

Persist the evidence packet and reviewer outputs under
`plans/investigations/INV-NNN/` so the analysis can be re-run or audited later.

## Local setup

```bash
task install  # uv sync → installs deps into .venv
task ci       # full local gate
task dogfood  # live pipeline against tests/fixtures/tiny_repo
```

See `README.md` for user-facing usage.

## Releasing

We use **PyPI Trusted Publishing** (OIDC) — no long-lived API token sits in
GitHub secrets. The release workflow lives at `.github/workflows/release.yml`.

### One-time PyPI side setup

Before the first release can publish successfully, register a Trusted Publisher
on PyPI:

1. Sign in at <https://pypi.org/> with the account that will own the project.
2. Go to **Account settings → Publishing → Add a new pending publisher**.
3. Fill in:
   - **PyPI Project Name:** `designdoc`
   - **Owner:** `SpillwaveSolutions`
   - **Repository name:** `docgen`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
4. Save. The first successful publish from the workflow will claim the project name.

Also create a GitHub Actions environment named `pypi` (Repo → Settings →
Environments → New environment → `pypi`). The workflow's
`build-and-publish` job is gated on this environment so reviewers can require
manual approval before publishing if desired.

### Cutting a release

```bash
# 1. Bump the version in pyproject.toml (e.g. 1.2.0 → 1.2.1).
#    The release workflow refuses to publish if the tag and pyproject version disagree.
$EDITOR pyproject.toml

# 2. Update CHANGELOG.md with the new version section.

# 3. Commit and merge via PR (no direct main commits — see PR Workflow above).

# 4. After merge, tag the release commit on main:
git checkout main
git pull --prune
git tag -a v1.2.1 -m "v1.2.1"
git push origin v1.2.1
```

Tag push triggers `.github/workflows/release.yml`, which:

1. Reruns the CI gate (lint + tests).
2. Verifies the tag matches `pyproject.toml`'s `version`.
3. Builds wheel + sdist with `uv build`.
4. Publishes to PyPI via OIDC Trusted Publishing.
5. Creates a GitHub Release with auto-generated notes and the built artifacts attached.

If publishing fails partway, fix the cause and re-run the workflow via
`Actions → release → Run workflow`, supplying the existing tag. The
`skip-existing: true` flag on the publish step makes re-runs idempotent.
