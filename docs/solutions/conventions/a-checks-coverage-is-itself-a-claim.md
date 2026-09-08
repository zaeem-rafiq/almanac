---
title: A check's coverage is itself a claim — say what it cannot see
date: 2026-09-08
category: conventions
module: verification-and-proofs
problem_type: convention
component: testing_framework
severity: high
applies_when:
  - "A proof line, CI gate, or validator will be reported as PASS to someone who did not write it"
  - "A document makes claims of more than one kind — numbers, mechanism, provenance — and the check covers only one kind"
  - "Deciding whether a class of claim is 'not mechanically checkable' before trying to check it"
tags: [coverage, scope, validators, proof-discipline, false-assurance, grounding]
---

# A check's coverage is itself a claim — say what it cannot see

## Context

A-10's proof harness reported `4/4 PASS` on `README.md`, including:

```
PROOF A-10: every numeric claim in README traces to a repo artifact (script check) = PASS   [39 numeric claims outside the verbatim block, all grounded]
```

While that was green, the same README stated:

> **The market rates are fetched via FMP** … `catalog.refresh_rates()` is the only FMP caller in
> the repo

Both halves were false. `catalog.FETCHERS` holds four fetchers — `nyfed`, `freddiemac`, `treasury`,
`bls` — and no FMP call exists in the rates path at all. ADR-000 §9 had dropped FMP's
`economic-indicators` back during **A-01**, because it returns a ~9-month-old window and cannot
produce `cpi_yoy` (two CPI rows two months apart — there is no year-ago pair). The documentation
never followed the code, and the claim shipped stale for most of the project's life.

The harness did nothing wrong. [`check_numbers()`](../../../scripts/proof_a10.py) grounds *numbers*,
and every number was genuinely grounded. It simply has no opinion about a sentence naming an API.

## Guidance

**A green check licenses a belief, and the belief a reader forms is broader than what the check
tested.** "Every numeric claim traces to an artifact" is read as "the claims in this document were
verified." Those are very different statements, and the gap between them is where a stale claim
lives undisturbed for months.

Two obligations follow:

1. **Write the scope into the proof line, not just the code.** A line that reports what it covers
   also reports, by omission, what it does not. `every numeric claim in README traces to a repo
   artifact` is honest precisely because "numeric" is in it — the reader can see that a claim about
   *mechanism* was never in scope. A line reading `README verified` would have been a lie told by
   a correct program.
2. **Before declaring a claim class uncheckable, try to check it.** Prose feels unverifiable, so it
   gets exempted by default. But a claim that *names a code entity* — an API, a module, a function,
   a service — is as mechanically checkable as a number:

   > The README names `FMP`. Does a live call to FMP exist under `almanac/`?

   A grep answers that in milliseconds — though it has to search for what a *live call* contains
   (the endpoint) rather than the service's name, since a name also matches the comments explaining
   its removal. See Examples, where the naive version is shown giving a false negative.
   **Not implemented here** — this doc records the available fix, not a shipped guard; the harness
   still grounds only numbers.

## Why This Matters

This is the third distinct way the same harness could report PASS while failing to protect
anything, and the three are worth reading together because they are not the same failure:

| doc | the question it asks | failure it prevents |
|---|---|---|
| [negative-controls-for-absence-checks](negative-controls-for-absence-checks.md) | *can this check fail at all?* | a check that is broken and therefore always green |
| [derive-dont-exempt-when-a-validator-flags-a-true-value](derive-dont-exempt-when-a-validator-flags-a-true-value.md) | *does the fix keep it able to fail?* | a check quietly disarmed while being made to pass |
| this one | *what can this check never see?* | a check that is working perfectly, on the wrong claims |

The first two are defects in the check. **This one is not a defect at all** — which is exactly why
it is the hardest to notice. There is no red line to investigate, no bug to fix, no control that
would have caught it. The only thing that catches a coverage gap is someone asking what the check
does *not* cover, and the proof line is the place to make that question answerable without reading
the source.

Note also which claim went stale: not an opinion or a judgement call, but a **provenance** claim —
where a number comes from. Provenance claims rot silently because the number they describe stays
correct. `mortgage_30y_fixed` was `6.71` before the switch and `6.71` after; only the sourcing
changed, and nothing that checked values would ever notice.

## When to Apply

- When writing a proof line: name the claim class it covers, so its silence about other classes is
  visible rather than assumed
- When a document mixes claim kinds — figures, mechanism, provenance, merge state — and the checker
  covers one kind
- Before writing off a claim class as unverifiable: if it names a file, module, API, or function,
  a grep can usually check it
- When reviewing an all-green report, ask "what would still be green if this were wrong?" — that
  question, not the report, is what finds coverage gaps

## Examples

The obvious check for "does the code still use FMP?" is to grep the name. **Run it, and it fails
to catch the bug:**

```bash
$ git grep -c "FMP" -- almanac/
almanac/catalog.py:1
almanac/cli.py:1
```

Two hits, so the naive check reports "still used" and stays green — but both hits are *comments
explaining that FMP was removed*. A mention is not a call, and searching for a name cannot tell
them apart. This is the same trap one level down: a check that looks like it covers the claim, and
does not.

The version that works looks for the thing a live call must contain — the endpoint — rather than
the word:

```bash
$ git grep -n -iE "financialmodelingprep|fmpcloud" -- almanac/
# (no output) -> nothing under almanac/ can reach FMP, whatever the prose says
```

Cross-checked against what *is* wired up, which is the positive half of the same question:

```bash
$ git grep -A5 "^FETCHERS" -- almanac/catalog.py | grep "_fetch_"
    "fed_funds_effective": _fetch_fed_funds_effective,
    "mortgage_30y_fixed": _fetch_mortgage_30y_fixed,
    "treasury_10y": _fetch_treasury_10y,
    "cpi_yoy": _fetch_cpi_yoy,
```

Contrast all of that with the check that was actually in place, which passed honestly and proved
something narrower:

```
39 numeric claims outside the verbatim block, all grounded
```

Every statement here was true at the same moment. Only the last one was on the report.

## Related

- [negative-controls-for-absence-checks](negative-controls-for-absence-checks.md) — can the check
  fail at all
- [derive-dont-exempt-when-a-validator-flags-a-true-value](derive-dont-exempt-when-a-validator-flags-a-true-value.md)
  — does a fix preserve the check's power
- [`scripts/proof_a10.py`](../../../scripts/proof_a10.py) — `check_numbers()`, whose docstring
  states its own scope
- [`docs/decisions/ADR-000-stack.md`](../../decisions/ADR-000-stack.md) §9 — why FMP was dropped
  during A-01, the change the documentation never followed
