# Investigation context: docgen HIL-issue root cause

## Claim being reviewed

My original observation (as the Claude Code assistant) after a full dogfood pipeline run:

> "Zero doer/checker retries across 70 invocations. Either the doers are extremely well-calibrated, or the checkers are letting through first-attempt output too easily. Combined with the high HIL count, it leans toward: doers write confidently, checkers object once, doer can't satisfy, ship to HIL. Healthy pattern — no wasted retry budget."

On further inspection I suspect this claim is wrong. Please validate or refute based on the evidence below.

## Context: the doer/checker loop architecture

The docgen project runs a Gen-3 harness-engineered pipeline. Every LLM-generated artifact goes through a 3-attempt doer/checker loop (`src/designdoc/loop.py`). The checker is a *separate* LLM with its own system prompt; it reads the doer's output and emits a JSON `CheckerVerdict`. After 3 failed attempts, the artifact ships with an inline `<!-- HIL: HIL-XXX -->` marker and the dispute is appended to `hil-issues.yaml`.

CLAUDE.md invariant #4: "Checker verdicts are schema-validated. Malformed JSON → synthetic `fail` verdict (see `verdict.parse_verdict`). Fail loud, not quiet."

## Evidence — hil-issues.yaml (abridged)

All 8 issues from a complete pipeline run. Note the `checker_said` field on every single one:

```yaml
- id: HIL-001
  artifact: src/tiny/payments/gateway.py::StripeGateway
  stage: class_docs
  checker_said: 'checker output unparseable: JSONDecodeError'
  attempts: 3
  suggested_fixes:
    - re-run checker — previous output failed to parse

- id: HIL-002
  artifact: package:reporting
  stage: package_rollups
  checker_said: 'checker output unparseable: JSONDecodeError'
  attempts: 3

- id: HIL-003 (package:payments) - same pattern
- id: HIL-004 (mermaid:Charge) - same pattern
- id: HIL-005 (mermaid:StripeGateway) - same pattern
- id: HIL-006 (mermaid:Report) - same pattern
- id: HIL-007 (dep:requests, tech_debt) - same pattern
- id: HIL-008 (system:rollup) - same pattern
```

Every single HIL issue across 5 different checker types (class_docs, package_rollups, mermaid, tech_debt, system_rollup) has:
- `checker_said: 'checker output unparseable: JSONDecodeError'`
- `attempts: 3`
- `suggested_fixes: re-run checker — previous output failed to parse`

One particularly revealing detail — HIL-008's `doer_said` ends with: *"The checker's parse failure on the previous output was likely a delimiter-boundary issue — the `<<<TAG>>>` sentinels must appear on their own lines..."*. The doer itself observed the checker's parse was failing, and tried to adapt its own output format in response — but the parse failure was on the CHECKER's output, not the doer's, so the doer's adaptation couldn't help.

## Evidence — parse_verdict implementation

`src/designdoc/verdict.py` lines 50-78:

```python
def parse_verdict(raw: str, *, attempt: int, artifact_id: str) -> CheckerVerdict:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError(f"expected JSON object, got {type(data).__name__}")
        data["attempt"] = attempt
        data["artifact_id"] = artifact_id
        return CheckerVerdict(**data)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as e:
        return CheckerVerdict(
            status="fail",
            # ...
            summary=f"checker output unparseable: {type(e).__name__}",
            issues=[CheckerIssue(
                severity="critical",
                location="<checker-output>",
                current_text=raw[:200],
                suggested_fix="re-run checker — previous output failed to parse",
            )],
        )
```

## Evidence — checker prompts uniformly instruct "JSON only"

Every checker prompt in `src/designdoc/agents/prompts.py` ends with some variant of:
- "Respond with ONLY the JSON object, no prose, no code fences."
- "Return only the JSON."
- "Return only the JSON. No prose, no code fences."

Despite this, every checker across every stage is emitting unparseable output that triggers the synthetic-fail path.

## Run statistics

- 70 LLM invocations total
- 0 total_retries reported
- 8 HIL issues raised, ALL with identical JSONDecodeError failure
- Cost: $4.93

## Questions for second-opinion review

1. Is my revised analysis correct — that the "zero retries" count is misleading because the retries DID happen (3 attempts × ~8 artifacts = ~24 retry cycles worth of work) but the loop counted them as checker-invocation failures rather than doer retries?
2. Is the "healthy pattern" framing defensible under any interpretation, or is this clearly a calibration / prompt-engineering bug?
3. Recommended fix priority:
   a. Defensive parsing (strip code fences, regex-extract first `{...}` block) before `json.loads`
   b. Tighter checker prompts with explicit examples
   c. Use Anthropic's JSON-mode / tool-use to force schema compliance
   d. Some combination
4. What diagnostic would confirm the root cause — capturing the raw checker output to disk before parse, to see whether it has a code fence, prose preamble, or genuinely malformed JSON?
