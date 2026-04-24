# Future Improvements & Investigations

A running ledger of calibration bugs, prompt-engineering concerns, and latent design issues surfaced during dogfooding. Each entry captures the observation, the initial hypothesis, second-opinion validation (when obtained), and the recommended next step.

---

## INV-001 — Every HIL issue is a checker JSON-parse failure (not a content dispute)

**Surfaced:** 2026-04-21, during first full `task dogfood` + `designdoc resume --budget 5.00` run against `tests/fixtures/tiny_repo` on v1.2.0.

**Run stats:** 70 LLM invocations, $4.93 spent, **0 reported retries**, **8 HIL issues** — one per non-trivial artifact across 5 checker types (class_docs, package_rollups, mermaid, tech_debt, system_rollup).

### Original observation (under review)

> "Zero doer/checker retries across 70 invocations. Either the doers are extremely well-calibrated, or the checkers are letting through first-attempt output too easily. Combined with the high HIL count, it leans toward: doers write confidently, checkers object once, doer can't satisfy, ship to HIL. Healthy pattern — no wasted retry budget."

### Revised analysis (prompted by re-reading `hil-issues.yaml`)

The "healthy pattern" framing is **wrong**. Every single one of the 8 HIL entries has:

```yaml
checker_said: 'checker output unparseable: JSONDecodeError'
attempts: 3
suggested_fixes: [re-run checker — previous output failed to parse]
```

This is the synthetic-fail verdict path in `src/designdoc/verdict.py:50-78` firing — `parse_verdict` catches a `JSONDecodeError`, `ValidationError`, `ValueError`, or `TypeError` from `json.loads(raw)` and returns a fabricated fail verdict per CLAUDE.md invariant #4 ("Malformed JSON → synthetic fail verdict. Fail loud, not quiet").

The doer isn't "objecting once and shipping" — the checker is never successfully parsed. Every attempt's verdict is a synthetic fail with summary `"checker output unparseable: JSONDecodeError"`. The loop exhausts 3 attempts and ships to HIL because the checker's *format* is broken, not because the doer's *content* failed review.

One smoking gun: HIL-008's doer even tried to adapt its output for what it (incorrectly) perceived as a parse issue on its own side: *"The checker's parse failure on the previous output was likely a delimiter-boundary issue — the `<<<TAG>>>` sentinels must appear on their own lines..."*. The doer was chasing a phantom; the parse failure was upstream of it.

### Why this matters

1. **Observability is misleading** — the `total_retries: 0` stat in `designdoc status` counts a narrow class of doer self-retry, not checker-parse retries. A reader interprets it as "nothing went wrong"; the truth is 8 artifacts × 3 attempts = **24 attempt executions**, i.e. **16 retries beyond the first attempt** that bought zero signal. (Math correction courtesy of Codex second opinion.)
2. **Cost is inflated ~2x** — every halted artifact cost 3 checker invocations that produced zero signal. At $4.93 for tiny_repo, a substantial fraction bought synthetic-fail verdicts.
3. **HIL is swamped with noise** — all 8 disputes are the same "re-run checker" placeholder. A real semantic dispute would drown in this noise, defeating the HIL invariant's purpose.
4. **The checker's JSON-schema guarantee is statistical, not structural** — every checker prompt says *"Return only the JSON. No prose, no code fences."* but the LLM violates this reliably across all 5 checker types. Prompt-level enforcement is insufficient.

### Root-cause hypotheses to confirm

H1. Checkers are wrapping JSON in markdown code fences (```` ```json ... ``` ````).
H2. Checkers are prepending a prose preamble ("Here's my verdict:").
H3. Checkers are appending trailing prose after the JSON.
H4. Checkers are emitting multiple JSON objects when multiple issues are found (newline-separated) rather than a single object with an `issues` array.
H5. Model-version drift in the bundled `claude` CLI — a newer model is more chatty than the one the prompts were calibrated against.

### Recommended diagnostic

Instrument `ClaudeSDKRunner` (or `doer_checker_loop`) to write each raw checker output to `<output>/.designdoc-debug/<artifact_id>__attempt<N>.txt` before `parse_verdict` runs. One dogfood re-run would reveal which hypothesis holds and direct the fix.

### Recommended fixes (ranked)

1. **Defensive parsing in `parse_verdict`** — strip markdown code fences and regex-extract the first balanced `{...}` block before `json.loads`. Low risk, high leverage. Keeps the existing strict path as a fallback.
2. **Capture raw output on parse failure** — persist `raw` to disk alongside the synthetic fail verdict so post-hoc analysis doesn't require re-running. Already partially done (`current_text=raw[:200]` is truncated; store the full output).
3. **Checker prompt retry with "your last output was not valid JSON"** — on attempt 2/3, prepend the previous raw output + a corrective instruction rather than re-running the same prompt verbatim. Attempts 2/3 currently can't fix the problem because they don't see the previous failure.
4. **Switch checkers to Anthropic tool-use / structured-output mode** — force schema compliance at the API level. Higher effort; best long-term.

### Existing invariants to respect

- **MAX_ATTEMPTS = 3** (CLAUDE.md #3) — any fix must preserve the exact-3-attempts cap. The "retry with corrective instruction" fix adapts attempt content, not count.
- **No self-grading** (CLAUDE.md #2) — the corrective-instruction retry must feed the checker its *own* prior raw output, not the doer's scratchpad, or the isolation invariant breaks.
- **Fail loud, not quiet** (CLAUDE.md #4, `parse_verdict` docstring) — defensive parsing must not swallow genuine schema violations; only strip wrappers that we can prove the checker added.

### Second opinions

Both Gemini 2.5 Pro and Codex CLI were given the same evidence packet at `/tmp/docgen_hil_investigation_context.md` and asked to validate or refute the revised analysis.

#### Gemini 2.5 Pro (full response: `/tmp/gemini_second_opinion.md`)

> "Yes, your revised analysis is correct. The 'zero retries' metric is dangerously misleading. … The 'healthy pattern' framing is indefensible. It is a severe misinterpretation of a critical system-wide bug. … This is a classic prompt-engineering and output-parsing bug. The uniform failure across five different checker types points to a systemic flaw, not a series of isolated content disputes."

Fix ordering (Gemini): **a → c → b**. Defensive parsing first as tactical stabilization, then forced-JSON-mode as strategic solution, then tighter prompts as "good practice but least reliable."

Diagnostic: *"The diagnostic you proposed is precisely the correct one. … The current `current_text=raw[:200]` in the synthetic verdict is insufficient. Log the full, unmodified `raw` response."*

#### Codex CLI (full response: `/tmp/codex_second_opinion.md`)

> "Yes, your revised analysis is directionally correct, and the original 'zero retries' read was wrong. … `parse_verdict` turns malformed checker output into a synthetic fail, so the loop cannot distinguish 'real quality objection' from 'checker broke its own protocol.' … Plainly: this is a prompting / output-contract / structured-generation bug, not a healthy no-waste retry pattern."

Fix ordering (Codex): **c → a → b**. *"If Anthropic JSON mode or tool-use is available, use it. The checker's only job is to emit a machine-readable verdict. This is exactly the class of problem structured output is meant to solve."* Defensive parsing as safety net. Prompt tightening alone is "not a serious fix here" given the instructions already say "JSON only" and failed uniformly anyway.

**Important caution from Codex:** *"Do not make defensive parsing so aggressive that it silently accepts truncated or ambiguous output. If you repair output, log that repair explicitly."* This aligns with CLAUDE.md invariant #4 ("fail loud, not quiet") — defensive parsing must be observable, not silent.

Both reviewers independently endorse the raw-output capture as the decisive next diagnostic step, and both note that HIL-008's doer trying to fix its own output to address a checker-side parse failure is particularly damning.

#### Convergent conclusions

- Original "healthy pattern" claim: **rejected** by both reviewers.
- Revised analysis (systemic checker contract failure): **endorsed** by both reviewers.
- Math correction: 24 attempt executions = 16 retries beyond the first attempt (Codex).
- Disagreement: Gemini prioritizes defensive parsing first, Codex prioritizes forced-JSON structured output first. Either ordering is defensible; the *diagnostic* step (capture raw output) is unanimous and should precede both fixes to confirm which failure mode dominates.

### Suggested action items (for a future v1.2.x or v1.3 plan)

- [x] Add per-attempt raw-checker-output capture (`DESIGNDOC_DEBUG_DIR` env var → `loop.py`). Done 2026-04-21.
- [x] Extend `parse_verdict` with a defensive code-fence-strip pass. Done 2026-04-21.
- [x] Add unit test fixtures of real-world malformed checker outputs. Done — `tests/unit/test_verdict.py` gained 6 fence-behavior tests.
- [ ] Run a full end-to-end dogfood to confirm 0 HILs post-fix (replay on captures already proved 9/9 false failures now parse as pass).
- [x] Distinguish "checker parse retries" from "doer content retries" in `total_retries` stat. Done 2026-04-23 — split into `doer_content_retries` + `checker_parse_retries` (PR #7).
- [ ] Consider migrating high-value checker paths to Anthropic's tool-use JSON mode (Codex's preferred long-term fix; not required to resolve INV-001).

### Diagnostic findings (2026-04-21)

Re-ran tiny_repo dogfood with `DESIGNDOC_DEBUG_DIR=/tmp/docgen-captures`. Budget halted at Stage 6 (mermaid) after collecting **12 checker captures** from Stage 3 (class_docs) and Stage 4 (package_rollups).

| Stage | Artifact | Captures | Result |
|---|---|---|---|
| 3 | Charge | 1 | pass attempt 1 |
| 3 | Report | 2 | fail attempt 1, pass attempt 2 |
| 3 | StripeGateway | 3 | all fail → HIL |
| 4 | package_payments | 3 | all fail → HIL |
| 4 | package_reporting | 3 | all fail → HIL |

**Every single one of the 9 failing captures starts with the exact same 20 bytes:** `` ```json\n{\n  "status" ``. No prose preambles, no truncation, no multiple-JSON-objects, no non-JSON tool-use wrappers. Just ```` ```json ```` code fences around otherwise-valid JSON.

**Replay evidence.** Feeding the 9 failing captures' raw outputs through the fixed `parse_verdict`:

```
9/9 previously-failing captures now parse as "pass"
0/9 parse as "fail" (genuine objections) — there were no genuine objections
```

Had the fence-strip been in place during the original v1.2 dogfood run:
- 8 → **0 HIL issues**
- 16 retries beyond first attempt → **0 wasted retry attempts** (all checkers would have passed attempt 1)
- Cost savings on tiny_repo: ~30% (8 artifacts × 2 wasted retry-pairs × ~$0.10/call ≈ $1.60 of the original $4.93)

### Fix summary

Single commit scope. Four files touched:

| File | Change |
|---|---|
| `src/designdoc/verdict.py` | Exposed `SYNTH_FAIL_PREFIX` constant. Added `_CODE_FENCE_RE` + `_strip_code_fence(raw)` helper. `parse_verdict` now feeds the stripped output to `json.loads`. Non-destructive: unfenced inputs pass through unchanged. |
| `src/designdoc/loop.py` | Added `debug_dir: Path \| None = None` parameter to `doer_checker_loop`, `DESIGNDOC_DEBUG_DIR` env-var fallback via `_resolve_debug_dir`, and `_capture_checker_output` helper. Opt-in; default behavior unchanged. |
| `tests/unit/test_verdict.py` | +6 tests covering fence-strip happy path, fence-wrapped fail verdicts, regression guards for clean-JSON and genuinely-malformed-inside-fence. |
| `tests/unit/test_loop.py` | +4 tests covering debug-capture on pass/fail, env-var support, explicit-parameter-overrides-env-var precedence. |

Preserves all existing invariants:
- `MAX_ATTEMPTS = 3` unchanged (CLAUDE.md #3).
- Checker isolation unchanged (CLAUDE.md #2) — defensive parsing is purely syntactic.
- "Fail loud" preserved (CLAUDE.md #4) — genuinely malformed JSON inside a fence still routes to synthetic-fail.

### Evidence artifacts

- `plans/investigations/INV-001/docgen_hil_investigation_context.md` — original evidence packet sent to reviewers.
- `plans/investigations/INV-001/gemini_second_opinion.md` — Gemini 2.5 Pro review.
- `plans/investigations/INV-001/codex_second_opinion.md` — Codex CLI review.
- `/tmp/docgen-captures/` — 12 JSON captures from the diagnostic dogfood (ephemeral; move to `plans/investigations/INV-001/captures/` if persisting).
