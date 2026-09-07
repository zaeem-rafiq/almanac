# A-04 — `judge.py`, rules R1–R5

- Drafted 2026-09-07, immediately after A-03 closed.
- **Caveat on provenance:** the HAC-40 issue text was not in hand when this was written. Everything
  below is derived from the repo — `evals/claims.jsonl`, `facts/catalog.yaml`, the Project Rules,
  and A-03's findings — and must be reconciled against the real issue before implementation.
  Where this plan asserts a number, the number was **measured**, and the measurement is shown.
- Pre-conditions: A-03 CLOSED, 4/4 proofs PASS. `venv/bin/pytest tests/ -q` → **82 passed**.

## 0. What A-04 inherits

| Input | State |
|---|---|
| `Claim` | 7 `claim_type`s, `entity_key` gated to catalog keys, `value`, `unit`, `year_hint`, verbatim `quote`, `locator` |
| Extraction quality | 54/54 eval rows covered, `claim_type` 52/54 (96%), `entity_key` 54/54 (100%) |
| `facts/catalog.yaml` | 15 entities, 0 nulls including history rows, every `source_url` 200 |
| `evals/claims.jsonl` | 54 rows, 5 statuses: skip 32, correct 9, stale_material 7, unresolved 4, stale_immaterial 2 |

Because extraction agreement is already at 96/100%, **an A-04 miss is a rule bug, not an extraction
bug.** That is a far better debugging position than A-03 started from, and it is the reason the eval
set can serve as A-04's scoreboard.

## 1. The principle, restated for this module

`judge.py` calls no model. Every verdict originates here and carries `rule_fired`. A-03 built the
input side of that contract — the model labels, the code admits — and A-04 is the half that decides.
A verdict without a `rule_fired` is a bug, not a default.

## 2. What the labels actually say — measured, not assumed

Extraction was run over every source in `evals/claims.jsonl` and each labelled row joined to its
`entity_key`'s current catalog value. Three findings, and two of them overturn the obvious design.

### 2a. `correct` is exact. No tolerance band.

All 9 `correct` rows have a difference of **exactly 0.00**. There is no "close enough" case in the
label set, so `correct` is equality, not proximity.

### 2b. A single materiality threshold CANNOT reproduce the labels — this is the plan's key finding

| status | n | relative difference | absolute difference |
|---|---|---|---|
| `stale_material` | 7 | −27.0% … **−2.17%** | 1.15 … 1,500.00 |
| `stale_immaterial` | 2 | **+2.09%** | 0.14 |

The two bands **overlap at the boundary**: 2.17% is labelled material and 2.09% is labelled
immaterial. They are 0.08 percentage points apart. Any global relative threshold that separates them
is fitting noise, and any global absolute threshold is worse (1.15 is material, 0.14 is not, but
200.00 is also material).

What separates them is **`kind`**:

- every `stale_material` row is a `statutory_limit`, except one `market_rate` that moved −27%
- both `stale_immaterial` rows are the same `market_rate` drifting +0.14pp

That is a real distinction, not a curve fit. A statutory limit is an **exact-match domain** — if the
video says 23,000 and the number is 24,500, a viewer who acts on it contributes the wrong amount, and
the size of the gap is irrelevant. A market rate is a **drift domain** — rates move constantly, the
video's argument survives a small move, and only a large move invalidates it.

**Consequence for R5:** materiality is kind-aware.
`statutory_limit` / `tax_bracket` → any difference is material.
`market_rate` → material only past a per-kind threshold, which must sit between **0.14pp and 1.15pp**
absolute (equivalently between 2.1% and 27% relative). Absolute percentage points is the better unit
— these facts are quoted in `pct` and a reader thinks in points, not in percent-of-a-percent.
**Proposed: 0.50pp**, comfortably inside the observed gap at both ends.

This is A-01's §5b lesson repeating: a single global threshold was wrong for freshness, and it is
wrong for materiality. Both times the fix was to make the threshold per-series/per-kind.

### 2c. `unresolved` is an absence, not a comparison

All 4 `unresolved` rows are `claim_type=statutory_limit` with `entity_key=None` — the combined
employee+employer ceiling, the HCE threshold, the key-employee threshold. Real statutory numbers that
Almanac's catalog simply does not carry. There is nothing to compare, so no comparison rule can fire.

This is why the five statuses do **not** map one-to-one onto five rules. `unresolved` is the
*absence* of a firing comparison; `skip` is produced by more than one rule; `stale_material` and
`stale_immaterial` are two outcomes of one rule. Anyone writing R1–R5 as a 1:1 status map will get
this wrong.

### 2d. The sharpest row in the eval set, and it only just became testable

| quote | type | key | claimed | current | Δ vs current |
|---|---|---|---|---|---|
| "Back in 2022 it paid nine point six two percent" | `historical` | `ibond_composite_rate` | 9.62 | 4.26 | **+125.8%** |

Expected status: **`skip`**. A judge that compares this against the *current* value sees a 126%
divergence and fires the loudest stale verdict in the corpus — on a sentence that is completely
accurate. This is A-03's finding F-2a made concrete, and it is the single best regression test in
the repo.

It is also **only checkable as of today**: the 2022 history row (9.62, 2022-05-01 → 2022-10-31) was
added at the end of A-03. Before that, the catalog had no window covering 2022, so the correct
behaviour could not be distinguished from the trap.

## 3. Proposed rule set — ordered, first match wins

Ordering is the design. Each rule is a guard that either fires or defers.

| # | Rule | Fires when | Verdict |
|---|---|---|---|
| R1 | `unjudged_claim_type` | `claim_type` ∉ {statutory_limit, tax_bracket, market_rate} | `skip` |
| R2 | `no_resolvable_fact` | judged type, but `entity_key is None` or the resolved `Fact.value is None` | `unresolved` |
| R3 | `historical_window_match` | `claim_type == historical`, or `year_hint` names a past window — resolve against `catalog` **history**, not `current` | `skip` on match |
| R4 | `matches_current` | claimed value equals the current value exactly | `correct` |
| R5 | `diverges_from_current` | claimed ≠ current — materiality by `kind` (see §2b) | `stale_material` / `stale_immaterial` |

R3 sits **before** R4/R5 deliberately: it is the guard that stops §2d from becoming a false verdict.

**R3's non-obvious half — the F-2a trap.** When `year_hint` names a window the catalog does *not*
cover, R3 must yield `unresolved`, never a divergence. "No covering history row" is missing evidence,
not a contradiction. Reading it as "the creator was wrong" puts a confident false verdict on an
accurate sentence, citing a primary source — the exact failure the product exists to prevent.

Every verdict carries `rule_fired` set to the rule's name, so a wrong verdict is traceable to one
line of code rather than to a vibe.

## 4. Steps, each with its check

| # | Step | Check |
|---|---|---|
| 1 | `Verdict` model in `models.py` — **needs an unprotect, `models.py` locked at the end of A-03** | see Open question Q1 |
| 2 | R1–R5 in `judge.py`, no model import | `grep -L anthropic almanac/judge.py`; unit tests per rule |
| 3 | Per-rule unit tests, including R3's missing-window branch | `venv/bin/pytest tests/test_judge.py -q` |
| 4 | Eval scoring harness over all 54 rows | prints confusion matrix; asserts **count checked == 54** |
| 5 | `python -m almanac judge <path>` | table with a `rule_fired` column on every row |
| 6 | Regression | `venv/bin/pytest tests/ -q` ≥ 82 passed |

## 5. Proof lines to satisfy (to be reconciled with HAC-40)

1. Every verdict carries a non-empty `rule_fired`; **count asserted equal to the claim total**.
2. The 2022 ibond historical claim yields `skip`, not a stale verdict — §2d's trap, asserted directly.
3. A judged claim whose `entity_key` is `None` yields `unresolved`, and the run produced ≥1 of them.
4. `judge.py` imports no model client — enforced by a test, not by inspection.
5. Eval scoring over all 54 rows, with the checked count asserted.

Per the A-01/A-03 lessons: proof 3 is a negative-shaped claim, so it also asserts the population is
non-empty; proofs 1 and 5 assert the **size** of what was checked, not merely that checking passed.

## 6. Open questions for Zaeem

**Q1 · `models.py` is protected as of the end of A-03, and `Verdict` has to live somewhere.**
Either unprotect it for A-04, or `judge.py` defines `Verdict` locally. Recommend the latter: it keeps
the A-03 lock intact and puts the verdict shape in the module that owns verdicts.

**Q2 · Confirm the market-rate materiality threshold.** §2b measures the gap as 0.14pp … 1.15pp and
proposes **0.50pp**. Two labelled rows is thin evidence for a constant; a per-key threshold in
`catalog.yaml` alongside `max_age_days` would be better-founded than one global number, at the cost
of more manual fields.

**Q3 · Does a stale *feed* change a verdict?** A-01 settled that `stale` annotates and never
suppresses, and that a stale fact still carries a usable value. Assumed to hold here: judging
proceeds and the report says "as of <date> (stale feed)". Confirm, because the alternative — suppress
verdicts on stale feeds — is a materially different product.

**Q4 · Should `unresolved` claims surface to the creator at all,** or only in the internal report?
Four of 54 rows are real statutory numbers Almanac does not carry; telling a creator "we could not
check this" may be more useful than silence, but it is a product call.
