---
title: An absence-asserting check proves nothing until a negative control makes it fail
date: 2026-09-07
category: conventions
module: verification-and-proofs
problem_type: convention
component: testing_framework
severity: high
applies_when:
  - "A check asserts that something is NOT present: no broken links, no ungrounded claim, no secret in the diff, no drift from a generated file"
  - "A proof line will be recorded as PASS in docs/proofs/ and later trusted by someone who did not watch it run"
  - "A safety guard is being written or extended (a hook, a lint rule, a CI gate)"
tags: [negative-control, proof-discipline, vacuous-pass, verification, guards]
---

# An absence-asserting check proves nothing until a negative control makes it fail

## Context

A-10 (HAC-46) required four proof lines, three of which assert an **absence**: no broken links in
the README, no numeric claim that fails to trace to a repo artifact, no drift between the README's
pasted block and `evals/results.md`. An absence check has a failure mode a presence check does not:
when it is broken, it reports PASS. Nothing in the transcript distinguishes "there is nothing wrong"
from "this check cannot see anything at all."

The issue text made the requirement explicit — *a check asserting an absence must first demonstrate
it can register a presence, or it passes vacuously*. That clause is not in
[`.agents/rules/almanac.md`](../../../.agents/rules/almanac.md), whose proof rule (line 13) governs
only the PASS/FAIL line format; the vacuity requirement came from the issue.

## Guidance

For every check whose PASS means "X is absent", write a **negative control**: corrupt a copy of the
input so X is present, run the same check, and record that it FAILED. Only then does the PASS on
the real input carry information.

The controls used in A-10, each run against a corrupted copy of `README.md` that was restored
afterwards:

| control (deliberate corruption) | the check must FAIL, and did |
|---|---|
| matched rows written as `47` instead of `46` | `UNGROUNDED: 47` |
| `](LICENSE)` changed to a path that does not exist | `BROKEN: ... -> no such file` |
| `**GATE: PASS**` edited inside the pasted verbatim block | `the pasted block is NOT byte-identical to evals/results.md` |

Record the control output next to the PASS in the proof artifact, not only in the transcript — the
proof doc is what a reader trusts six months later.

## Why This Matters

**Both controls found real bugs in the checker before they found anything in the artifact under
test.** Neither would have been visible from a green run:

1. `ISO_DATE` was `\b\d{4}-\d{2}-\d{2}\b`. It never matched `2026-09-08` inside
   `evals/results.json`'s `"run_at": "2026-09-08T01:53:14+00:00"`, because `T` is a word character
   so the trailing `\b` cannot hold. The date in the README could not ground against the very file
   that contained it. Fixed to a digit-lookaround at
   [`scripts/proof_a10.py:35`](../../../scripts/proof_a10.py).
2. The README used a typographic minus (U+2212) in `-2.17%`. The number regex accepted only an
   ASCII hyphen, so the value parsed as `+2.17` and could not match `judge.py`'s `-2.17%`. Fixed by
   normalising U+2212 and U+2013 before parsing, at
   [`scripts/proof_a10.py:56-58`](../../../scripts/proof_a10.py).

Without the controls, both bugs ship as green checkmarks and the proof line
"every numeric claim traces to a repo artifact = PASS" becomes a sentence about nothing.

The same reasoning extends to safety guards, which are absence checks with teeth: a guard's PASS is
"no dangerous command got through." Later in the same session, a recursive force delete was blocked
by `~/.claude/hooks/require-confirm-destructive.py` while `python3 -c "shutil.rmtree(...)"` — the
identical irreversible operation — ran unimpeded. The guard had never been shown to fire on the
routes that actually existed. Closing it needed both a new pattern
(`~/.claude/hooks/require-confirm-destructive.py:50-74`) **and** a live demonstration that the
previously-passing command now blocks.

## When to Apply

- Any proof line phrased as "no X", "0 X", "every X is Y", or "nothing that isn't Z"
- Before recording a PASS that a future reader will not re-derive for themselves
- When writing or widening a guard, hook, lint rule, or CI gate — prove it fires on the escape
  route you believe you just closed, using the real invocation path, not a simulated payload
- **Not** needed for presence assertions (`stale_material >= 1`, `278 passed`), which fail loudly on
  their own when the machinery is broken

## Examples

The control loop, as run: mutate, assert FAIL, restore.

```bash
run_control () {  # $1 = label, $2 = mutation applied to README.md
  cp README.md /tmp/README.bak
  python3 -c "$2"
  out=$(python3 scripts/proof_a10.py 2>&1)
  echo "--- control: $1"
  echo "$out" | grep -E "FAIL" || echo "    !! NO FAIL - the check is vacuous"
  cp /tmp/README.bak README.md
}
```

The `|| echo "!! NO FAIL - the check is vacuous"` branch is the point of the whole construct: a
control that does not produce a FAIL is itself the finding.

For a guard, the equivalent is exercising the real path rather than a hand-built payload. Feeding
17 synthetic payloads to the hook proved the patterns matched; only re-issuing the exact command
shape that had previously succeeded proved the guard was actually wired in front of it.

## Related

- [derive-dont-exempt-when-a-validator-flags-a-true-value](derive-dont-exempt-when-a-validator-flags-a-true-value.md) — extends this rule: the control
  belongs after every *widening* of a check, not only at its first writing
- [a-checks-coverage-is-itself-a-claim](a-checks-coverage-is-itself-a-claim.md) — the third
  question: what this check can never see, however well it works
- [`docs/proofs/A-10.md`](../../proofs/A-10.md) — the four proof lines and their controls
- [`scripts/proof_a10.py`](../../../scripts/proof_a10.py) — the harness, with both bug fixes
- [`docs/walkthroughs/A-10.md`](../../walkthroughs/A-10.md) — why two proof lines were deliberately
  scoped wider than the issue's wording, and said so rather than quietly claiming the narrower check
