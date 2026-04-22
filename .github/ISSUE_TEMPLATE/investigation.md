---
name: Investigation (INV-NNN)
about: Non-trivial behavior worth understanding before acting. Paired with an INV entry in plans/future_improvements_investigations.md.
title: "INV-NNN: <short description>"
labels: [investigation]
---

<!-- Investigation issues track non-obvious findings that deserve analysis and
second-opinion validation before a fix lands. The full analysis, evidence,
and reviewer second opinions live in plans/future_improvements_investigations.md
and plans/investigations/INV-NNN/. Keep this issue concise; link to the plan. -->

## Claim under investigation

<!-- The analytical claim being validated or refuted. State it plainly. -->

## Evidence pointer

<!-- Link to the evidence bundle: plans/investigations/INV-NNN/ and the
corresponding entry in plans/future_improvements_investigations.md. -->

- Plan entry: `plans/future_improvements_investigations.md#inv-nnn`
- Evidence: `plans/investigations/INV-NNN/`

## Second opinions

<!-- Non-obvious analytical claims should be validated by at least one external
reviewer (Gemini CLI, Codex CLI). Link their artifacts. -->

- [ ] Gemini 2.5 Pro review
- [ ] Codex CLI review

## Proposed action

<!-- Fix, instrumentation, prompt tightening, architectural change — or
"document and close" if the finding doesn't warrant a code change. -->

## Closing criteria

<!-- What must be true for this investigation to close? -->
