## Q1

Yes, your revised analysis is directionally correct, and the original "zero retries" read was wrong.

The evidence is explicit:

- Every HIL entry shows `attempts: 3`.
- Every HIL entry shows `checker_said: 'checker output unparseable: JSONDecodeError'`.
- `parse_verdict` converts any checker parse failure into a synthetic `status="fail"` verdict in `src/designdoc/verdict.py` lines 50-78.

That means the loop **did** burn through all three attempts on each affected artifact. What did **not** happen was a clean, parseable checker rejection that your `total_retries` metric apparently counts. The metric is therefore misnamed or incomplete: it is not measuring "attempts consumed by the loop," it is measuring only a narrower class of retry.

One correction to your arithmetic: 8 artifacts at 3 attempts each means **24 total attempt executions**, which is **16 retries beyond the first attempt**, not 24 retries. But the core conclusion stands: the pipeline spent substantial retry budget despite reporting `0 total_retries`.

## Q2

No. The "healthy pattern" framing is not defensible.

This is not evidence of well-calibrated doers and strict-but-useful checkers. It is evidence of a **systemic checker contract failure**:

- All 8 HILs across **5 different checker types** failed with the exact same `JSONDecodeError`.
- The checker prompts already say "JSON only," yet every checker still produced unparseable output.
- `parse_verdict` turns malformed checker output into a synthetic fail, so the loop cannot distinguish "real quality objection" from "checker broke its own protocol."

That means these HILs are not documenting substantive doer/checker disagreement. They are documenting that the checker channel itself is unreliable. The doer note in HIL-008 makes this even clearer: the doer tried to adapt its own formatting to fix a failure that was actually on the **checker output path**. That is wasted adaptation driven by bad feedback.

Plainly: this is a prompting / output-contract / structured-generation bug, not a healthy no-waste retry pattern.

## Q3

`d. Some combination`, with a clear ordering:

1. **`c` first: force structured output at generation time.**  
   If Anthropic JSON mode or tool-use is available, use it. The checker's only job is to emit a machine-readable verdict. This is exactly the class of problem structured output is meant to solve.

2. **`a` second: add defensive parsing as a safety net.**  
   Strip code fences, trim leading prose, and extract the first JSON object before `json.loads`. Do not rely on this as the primary fix, but it should exist because LLM output contracts fail in production.

3. **`b` last: tighten prompts only if the raw-output diagnostic shows a stable failure pattern.**  
   Prompt tightening alone is not a serious fix here. You already have repeated "JSON only" instructions across all checker prompts, and they still failed uniformly. That is strong evidence that prompt wording by itself is insufficient.

One caution: do not make defensive parsing so aggressive that it silently accepts truncated or ambiguous output. If you repair output, log that repair explicitly.

## Q4

Yes. Capturing the **raw checker output before parse** is the fastest diagnostic that will confirm the root cause.

That artifact should be written to disk with at least:

- `artifact_id`
- `stage`
- `attempt`
- checker model name
- raw response bytes/text exactly as returned
- parse exception type/message
- whether any repair logic was applied

That will tell you immediately whether the failures are:

- code fences around otherwise valid JSON
- prose preamble or epilogue
- truncated JSON
- multiple JSON objects
- non-JSON tool/use wrapper content
- genuinely malformed JSON

Given the evidence, that diagnostic is the decisive next step. Right now the system only preserves the synthetic summary `"checker output unparseable: JSONDecodeError"`, which is too lossy to debug the actual failure mode.
