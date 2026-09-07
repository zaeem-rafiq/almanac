# A-04 — judge / notes / report · plan

**Issue** HAC-40 · Milestone M1 · budget 1.5h / 40 turns.

> **Superseded in part, 2026-09-07.** Steps 1–2 of this plan (a `judge.py` written here, with
> `Verdict`/`Report` added to `models.py` under an ADR) were overtaken while the branch was open:
> Zaeem pushed his own `judge.py` to main as `609af65`, keeping `models.py` locked and defining
> `Verdict` inside `judge.py`. That decision stands. The branch was replayed onto it and the
> remaining steps — `notes.py`, `report.py`, `scan`/`lint`, the proof harness — were ported onto
> his shapes. See `docs/walkthroughs/A-04.md` for what actually shipped.

## The one thing this issue decides

A-03 shipped a model that *labels* sentences. A-04 is where the central claim becomes true or
becomes marketing: **the LLM reads, the code decides.** `judge.py` contains no model call and no
network call. Every verdict it emits carries a `rule_fired` naming the rule that produced it, so
any verdict can be re-derived by hand from the claim, the catalog row, and this file.

`notes.py` may call a model, but only to *phrase* a verdict `judge.py` already reached, and a code
assertion — not a prompt instruction — rejects a phrasing that drops the number or gives advice.

## Steps, each with its check

| # | Step | Check |
|---|------|-------|
| 1 | Extend `almanac/models.py` with `Verdict` (subclasses `Claim`) and `Report`/`VideoReport`. Record the extension as `docs/decisions/ADR-001-verdict-and-report-models.md`. | `venv/bin/python -c "from almanac.models import Verdict, Report"` |
| 2 | `almanac/judge.py`: `judge(claim, catalog)`, rules R1–R5 in order, thresholds as named constants. | `venv/bin/pytest tests/test_judge.py -q` |
| 3 | `almanac/notes.py`: `draft_note()` (model) + `check_note()` (code assertion). | `venv/bin/pytest tests/test_notes.py -q` |
| 4 | `almanac/report.py`: `Report` JSON + Markdown twin. | `venv/bin/pytest tests/test_judge.py -q -k report` |
| 5 | CLI `scan --source corpus|youtube --out reports/` and `lint <path>`. | `venv/bin/python -m almanac lint corpus/scripts/fresh_ok.md` |
| 6 | `scripts/proof_a04.py` — the four PROOF lines, live. | `venv/bin/python scripts/proof_a04.py` |
| 7 | Regression. | `venv/bin/pytest tests/ -q` (106 passing at start) |

## Rules, exactly as ordered

```
R1  claim_type in {illustrative, historical, other}   -> skip
R2  entity_key None, or not in catalog                -> unresolved
R3  confidence < MIN_CONFIDENCE (0.6)                 -> unresolved
R4  kind statutory_limit | tax_bracket:  value == current -> correct, else stale_material
R5  kind market_rate:  |claim - current| < 0.10 pct-pt -> correct
                                        < 0.50 pct-pt -> stale_immaterial
                                       >= 0.50 pct-pt -> stale_material
```

Two guards sit inside R4/R5 rather than beside them, because a rule that cannot compare must not
silently emit `correct`: a missing claim value, a missing current value, or a claim whose unit
contradicts the catalog's unit resolves to `unresolved` under the rule that was reached, and
`rule_fired` says which and why.

`R5`'s two thresholds are module constants `MATERIAL_PCT_POINTS = 0.50` and
`IMMATERIAL_PCT_POINTS = 0.10`; A-10 echoes them into the README rubric, so they are read from
`judge.py`, never retyped.

## Three things settled before writing code

1. **V2's mortgage verdict is `stale_immaterial` and that is correct.** History has
   `mortgage_30y_fixed = 6.85` for V2's publication week; current is 6.71; |Δ| = 0.14 lands in
   R5's middle band. The report shows the rule. The thresholds are not tuned to make the demo
   louder.
2. **The `structural` vocabulary gap is A-05's, not A-04's.** Four `evals/claims.jsonl` rows are
   labelled `expected_claim_type: structural`, a type the frozen `Claim` model cannot emit, so
   `extract.py` returns `other`. All four carry `entity_key: null` and `expected_status: skip`, so
   R1 or R2 reaches the right verdict either way. `models.py` and the labels stay untouched.
3. **A stale FACT is not a stale CLAIM.** `Fact.stale` annotates; it never suppresses. `cpi_yoy`
   reads stale by design (BLS dates CPI by reference month) and a cpi claim must still be judged.
   `Verdict.fact_stale` carries the annotation into the report.

## What the proofs must survive

* Proof 4 is a pure negative assertion (`grep` finds no verdict vocabulary in `extract.py`). A grep
  that matches nothing and a grep pointed at nothing look identical, so the harness first runs the
  same grep against a file that **does** contain the words and asserts it goes red. A-01 shipped
  this bug twice.
* Proof 2 asserts a property over "every stale_material verdict". The harness prints the count and
  asserts it is non-zero **before** asserting the property, so "all 0 of them passed" cannot pass.

## Protected

`almanac/models.py` — extended with `Verdict`/`Report` only, recorded in an ADR. `facts/**`,
`evals/**`, `corpus/**` read-only. A-06 owns `almanac/youtube.py`, `scripts/render_slides.py`,
`scripts/seed_channel.md` — untouched; `scan --source youtube` imports `youtube.py` lazily and
says so plainly when A-06 has not landed it.
