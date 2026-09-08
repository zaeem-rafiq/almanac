---
title: When a validator flags a value that is actually true, derive it — never exempt it
date: 2026-09-08
category: conventions
module: verification-and-proofs
problem_type: convention
component: testing_framework
severity: high
applies_when:
  - "A validator flags a claim you can see is correct, and the quickest fix is an allowlist entry"
  - "A checker matches literal tokens, but the claim under test is a sum, count, ratio, or other derived value"
  - "A grounding, lint, or policy check is being widened to make a failing case pass"
tags: [validators, allowlist, derived-values, grounding, false-negative, proof-discipline]
---

# When a validator flags a value that is actually true, derive it — never exempt it

## Context

A-10's proof harness asserts that every numeric claim in `README.md` traces to a repo artifact. It
grounds a number by looking for that literal token in a set of source files.

After the A-09 deploy went live, the README gained the sentence *"five videos, 83 verdicts, every
one carrying the rule that produced it."* The harness failed:

```
PROOF A-10: every numeric claim in README traces to a repo artifact (script check) = FAIL   [UNGROUNDED: 83]
```

The claim was **true**. `reports/latest.json` holds `{skip: 72, correct: 1, stale_material: 6,
stale_immaterial: 1, unresolved: 3}`, and 72+1+6+1+3 = 83. But `83` is a *derived sum*: it appears
as a literal token in no file in the repository, so a literal-matching checker could never ground
it. The obvious fix was one line — add `83` to
[`ALLOWED_UNGROUNDED`](../../../scripts/proof_a10.py) at line 22, next to the two entries already
there, and move on.

## Guidance

**An exemption does not make the check pass — it makes the check stop looking.** Adding `83` to the
allowlist would have passed `83`, and it would equally have passed `84`, `91`, or any other number
someone later typed in that sentence. The value the check was created to protect is exactly the one
it would stop protecting.

When a validator flags something you know is true, work out **why it cannot see it**, and fix that:

| the flag means | the fix |
|---|---|
| the value is genuinely wrong | fix the claim |
| the value is right, and derivable from an artifact | **teach the checker to derive it** |
| the value is right, and grounded in a file the checker does not read | add that file to the sources |
| the value is right and traces to nothing at all (a title, a port number) | *then* exempt it, with the reason recorded inline |

Only the last row earns an allowlist entry, and it is the rarest. Reach for it last, not first.

The fix here computes the totals instead ([`scripts/proof_a10.py:73`](../../../scripts/proof_a10.py)):

```python
def derived_from_report() -> set[float]:
    """Totals the README may legitimately quote that appear literally in no file."""
    report = json.loads((ROOT / "reports" / "latest.json").read_text(encoding="utf-8"))
    rows = [row for video in report.get("videos", []) for row in video.get("verdicts", [])]
    return {
        float(len(rows)),                                    # total verdicts
        float(sum(1 for r in rows if r.get("rule_fired"))),  # carrying a rule
        float(len(report.get("videos", []))),                # videos scanned
    }
```

The README's claim is now **checked against the report** rather than excused from checking. It got
stronger, not weaker, by being made to pass honestly.

## Why This Matters

The two fixes are indistinguishable from the transcript — both turn a red line green in one commit —
and they are opposites in effect:

| | exemption | derivation |
|---|---|---|
| `83 verdicts` (correct) | PASS | PASS |
| `84 verdicts` (wrong) | **PASS** | **FAIL** |

Proved rather than argued. Widening a grounding set is precisely the change most likely to make an
absence check vacuous, so the negative control was re-run immediately afterwards:

```
--- control: derived total 83 -> 84, must FAIL ---
PROOF A-10: every numeric claim in README traces to a repo artifact (script check) = FAIL   [UNGROUNDED: 84]
```

That control is what separates the two columns above. Without re-running it, an exemption and a
derivation leave identical evidence behind — see
[negative-controls-for-absence-checks](negative-controls-for-absence-checks.md), whose rule this
extends: **the control belongs after every widening, not only at the check's first writing.**

There is a second-order cost too. Allowlists are append-only in practice — nobody audits them — so
each entry is a permanent hole that a future, unrelated edit can fall into. `ALLOWED_UNGROUNDED`
keeps two entries (`60`, from the section title "Try it in 60 seconds", and `8000`, a port), each
carrying its reason as its dict value so the harness prints why when it skips one. Two is a number
a reader can still hold in their head. Twenty is a disabled check.

## When to Apply

- Any time the quickest path to green is adding to an allowlist, `# noqa`, `skip`, `expected_failures`,
  or a policy exception
- Whenever a check compares literals but the claim is a sum, count, percentage, or ratio
- Immediately after widening a grounding set, source list, or matcher — re-run the negative control
  before trusting the new PASS
- **Not** applicable when the value truly traces to nothing (prose figures, ports, section titles) —
  exempt those, but record the reason next to the entry

## Examples

The wrong fix, which passes any number:

```python
ALLOWED_UNGROUNDED = {
    83.0: "verdict count from the live report",   # <- also passes 84, 91, 1000
}
```

The right fix, which passes exactly the correct number — the full function is quoted above, and its
docstring records the reasoning at the point of use so the next reader does not re-litigate it:

```
reports/latest.json (derived)  ->  {83.0, 83.0, 5.0}
```

## Related

- [negative-controls-for-absence-checks](negative-controls-for-absence-checks.md) — the parent rule;
  this doc extends it from "control at first writing" to "control after every widening"
- [`scripts/proof_a10.py`](../../../scripts/proof_a10.py) — `derived_from_report()` and the two
  surviving `ALLOWED_UNGROUNDED` entries
- [`docs/proofs/A-10.md`](../../proofs/A-10.md) — the FAIL, the fix, and the re-run control in sequence
