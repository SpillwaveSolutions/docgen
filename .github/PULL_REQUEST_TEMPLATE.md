<!--
CLAUDE.md "PR Workflow (MANDATORY)" applies. Fill every section; delete
instructional comments before posting.
-->

## Summary

<!-- One or two sentences. What does this PR do? -->

## Why

<!-- Motivation / context. What problem or plan does this address? Assume the
reviewer has not read the linked issue. -->

## Changes

<!-- Bullet the notable changes. Reference file paths for non-obvious ones. -->

-

## Invariants

<!-- Cite any CLAUDE.md "Non-Negotiable Invariants" touched by this PR and
confirm preservation. If an invariant needed to change, link the discussion
that decided so. Delete this section only if the PR is pure docs/tooling
with no code-invariant interaction. -->

- [ ] MAX_ATTEMPTS=3 preserved (loop.py)
- [ ] Checker isolation preserved (no self-grading)
- [ ] Schema-validated verdicts / fail-loud preserved
- [ ] Mermaid two-checker (mmdc + LLM) preserved
- [ ] HIL fallback preserved

## Verification

- [ ] `task ci` green locally
- [ ] New tests cover the change
- [ ] TWRC discipline followed (test before implementation)

## Related

<!-- Issues closed or referenced by this PR. Use "Closes #N" for issues this
PR fully resolves; "Part of #N" for multi-PR work. -->

- Closes #
