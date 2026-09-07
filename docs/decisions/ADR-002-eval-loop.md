# ADR-002 — the eval loop, and what the first independent measurement found

**Status:** proposed 2026-09-07 (A-05 / HAC-41). Decisions D-1..D-4 below each carry their own
status. This file is append-only.

## Context

`python -m almanac eval` scores `extract` + `judge` against the 54 human labels in
`evals/claims.jsonl`. It is the first measurement of the pipeline made by something other than
the session that wrote the rules.

**The rule that governs this issue: nothing is tuned to make the table green.**
`evals/claims.jsonl` is not written by this branch — verified by
`tests/test_eval.py::test_run_never_writes_the_labelled_set`, which hashes the file around a full
run. `judge.MARKET_RATE_MATERIAL_PP` is not read by the harness and was not changed (D-4).

## What the run found

Live run, 2026-09-07, 7 sources → 77 verdicts:

| metric | value |
|---|---|
| rows matched | **48 / 54** |
| extraction recall | 89% |
| `claim_type` accuracy | 98% (47/48) |
| **status disagreements on matched rows** | **0** |
| hard-negative false positives | **0** over 22 matched illustrative/historical rows |
| `stale_material` | precision 1.00, recall 1.00 (7/7) |
| `correct` / `stale_immaterial` / `unresolved` | recall 1.00 each (9/9, 2/2, 4/4) |
| `skip` | precision 1.00, recall 0.81 (26/32) |

### On the claimed "54/54 agreement" (commit `609af65`)

Treated as a claim to verify, not a result to reproduce. The independent measurement **confirms
the judge and refines the figure**: on every labelled row the harness could pair with a verdict,
`judge.py` agrees — 48/48, zero disagreements, across all five statuses. It does not reproduce
54/54, because 6 labelled rows never receive a verdict of their own. That is an **extraction
granularity** gap, not a judge disagreement, and the two were previously reported as one number.

How rows were matched, since the figure depends on it: a labelled row pairs with a verdict when
the verdict's quote **covers ≥ 0.90 of the labelled quote** in normalised text (whitespace
collapsed, curly quotes straightened — extraction's own leniency, nothing looser), greedy and
one-to-one, ties broken by the tighter quote. See D-1.

Every non-`skip` status has perfect precision *and* recall. All 6 unmatched rows are
`expected_status: skip`. **The pipeline did not miss a single claim that needed a correction**,
and did not invent one.

---

## D-1 — the matcher measures coverage, not span identity

**Status:** AWAITING RATIFICATION · **Fix applied:** harness (`almanac/evaluate.py`)

**The miss.** The first harness matched by intersection-over-union at IoU ≥ 0.30 and reported 8
labelled rows as "not extracted". They had all been extracted. Example, line 5:

| | |
|---|---|
| label | “Say you make seventy thousand dollars” |
| verdict quote | “Say you make seventy thousand dollars and your plan matches six percent of your salary.” |
| IoU | 0.43 → scored a miss |
| coverage of the label | 1.00 → the claim was produced |
| status | expected `skip`, got `skip` |

**Which side was wrong: the harness.** A label is a short fragment a human chose to identify a
sentence; an extracted quote is the whole sentence. They are *supposed* to differ in length. IoU
answers "are these the same span?"; the question the metric owes us is "did the pipeline produce a
verdict for this labelled claim?", which is containment.

**Why this is not tuning to green.** Stated plainly because the distinction is the milestone:

1. **The gate already passed under the old criterion** — `stale_material` recall 1.00, 0
   hard-negative false positives. The change does not rescue a failing gate; it cannot buy a pass
   that was already held.
2. **It checks more rows, not fewer.** Matched rows rise from 46 to 48. The matched hard-negative
   count is 22 under both criteria — the two rows it gained (lines 42 `correct`, 51
   `stale_immaterial`) are not hard negatives — so the adversarial probe is no weaker and no
   stronger, and the gate is decided on the same 22 rows either way.
3. **It can reduce matches.** Coverage ≥ 0.90 rejects a verdict covering only part of a label,
   which IoU ≥ 0.30 would accept. Line 14 fails both.
4. **The floor is not load-bearing, and that is measured.** Best coverage is exactly 1.00 for 51
   of 54 rows, then a cliff to 0.67. `sensitivity` sweeps 0.50 / 0.70 / 0.90 / 1.00 every run and
   the report prints all four: **48 / 48 / 48 across the operating band**, and 49 at the 0.50
   probe. The probe is there on purpose — a "constant" sweep is only evidence when the sweep is
   capable of moving, and the report says so explicitly when it never moves.

**Zaeem's call:** ratify, or reject and have the harness report both numbers side by side.

---

## D-2 — 6 labelled rows get no verdict of their own

**Status:** AWAITING RATIFICATION · **Fix applied:** none yet — label or prompt, Zaeem's call

All 6 expect `skip`; all 6 are `illustrative`; in every case a verdict *does* cover or overlap the
sentence and that verdict is `skip`. The product outcome is identical either way — this is an
accounting difference, not a wrong answer. Two mechanisms:

**(i) Merged — the extractor emitted one claim where the labels count two.** The sibling row took
the pairing; one-to-one assignment then leaves this one unmatched.

| line | quote | swallowed by the verdict paired with |
|---|---|---|
| 5 | “Say you make seventy thousand dollars” | line 6 |
| 12 | “Say you have three hundred thousand dollars left on the loan” | line 13 |
| 27 | “you have no debt above eight percent” | line 26 |

**(ii) Split — the labelled quote crosses a sentence boundary the extractor cut on.**

| line | quote | best coverage |
|---|---|---|
| 14 | “an extra five hundred dollars a month you could throw at either side” | 0.31 (the covering quote hit `MAX_QUOTE_CHARS` and was trimmed mid-sentence) |
| 25 | “Ten thousand dollars. It showed up, it is yours” | 0.45 (verdict quotes “Ten thousand dollars.”) |
| 29 | “Whatever is left goes into three funds. Not thirty. Three.” | 0.67 (verdict quotes the first sentence) |

**My read — the LABEL side, for five of the six.** Lines 5/12/27 label a *number inside* a
sentence the extractor correctly quotes whole; lines 25/29 label a span crossing sentence
boundaries, which the extractor is designed not to do. Making extraction emit one claim per number
would raise this metric and lower quote quality, which is the wrong trade for a product whose
output a human reads. Line 14 is the one I would call SYSTEM: the covering quote was truncated by
`MAX_QUOTE_CHARS = 200` mid-sentence, so the second number in that sentence has no home.

**Do not act on my read.** Each row needs your yes/no; nothing has been changed.

---

## D-3 — one `claim_type` disagreement

**Status:** AWAITING RATIFICATION · **Fix applied:** none yet — label or prompt, Zaeem's call

| line | quote | labelled | model said | status |
|---|---|---|---|---|
| 23 | “you can pull that money out of the account tax free in twen…” | `illustrative` | `structural` | `skip` both ways ✓ |

`structural` is A-03's seventh label — "a rule about how this account behaves". A tax-free
withdrawal condition arguably *is* structural, so this may be the label to move rather than the
prompt. The status is unaffected: both route to `skip`. Cosmetic for the gate, real for the
`claim_type` accuracy figure the README will quote.

---

## D-4 — `MARKET_RATE_MATERIAL_PP` was not touched

**Status:** RECORDED (no change made) · **Fix applied:** none

The issue forbids tuning R5's threshold to the eval set. It sits at 0.50pp and this branch neither
reads nor changes it. Both `stale_immaterial` rows and the one `stale_material` market-rate row are
classified correctly at that value, so there is no pressure on it from this run. `judge.py`'s own
open question Q2 — that two labelled rows are thin evidence for a global constant, and a per-key
threshold in `facts/catalog.yaml` would be better founded — stands unchanged and unaddressed here.

---

## Consequences

* `evals/results.md` and `evals/results.json` are regenerated by every run; `results.md` is the
  table the README quotes.
* The gate is binary and lives in `evaluate.gate_failures`: any hard-negative false positive, or
  `stale_material` recall < 0.90, exits non-zero. Liveness is asserted first — sources read,
  verdicts produced, rows matched — so a run that produced nothing fails loudly instead of
  reporting a perfect zero.
* `--cache` persists one extraction. `docs/proofs/A-04.md` records 56–67 verdicts across otherwise
  identical runs, so without it a rule change and a resample are indistinguishable during review.

---

## D-5 — the pipeline does not reliably pass its own gate, and the gate is right to say so

**Status:** AWAITING RATIFICATION · **Fix applied:** none — this is a change to `extract.py`
(A-03's module) and needs Zaeem's call before it is made

**The finding.** The gate is not flaky because the gate is wrong. Extraction resamples on every
run, and on some runs it drops a `stale_material` claim. Five independent LIVE runs:

| run | verdicts | matched | `stale_material` | hard-neg FP | status disagreements | gate |
|---|---|---|---|---|---|---|
| 1 | 77 | 46/54¹ | 7/7 | 0 | 0 | PASS |
| 2 | 68 | 42/54 | 6/7 | 0 | 0 | **FAIL** |
| 3 | 65 | 42/54 | 6/7 | 0 | 0 | **FAIL** |
| 4 | 77 | 48/54 | 7/7 | 0 | 0 | PASS |
| 5 | 75 | 48/54 | 7/7 | 0 | 0 | PASS |

¹ run 1 was scored under the pre-D-1 IoU matcher; the rest under coverage.

**Two things this establishes, and they point in opposite directions.**

1. **`judge.py` is solid.** *Zero* status disagreements in all five runs, across all five statuses.
   Whatever extraction hands it, the judge rules correctly. This is the independent confirmation
   the "54/54" claim was reaching for, and it holds.
2. **`extract.py` is not reproducible.** Verdict counts range 65–77 on identical inputs, and the
   gate fails 2 runs in 5. `docs/proofs/A-04.md` already recorded 56–67 verdicts across runs; that
   variance was visible then and is now attached to a consequence.

**The row that drops is always the same: line 35.**

> `stale_material` · `market_rate` · `ibond_composite_rate`
> “The composite rate right now is three point one one percent”

It sits immediately before line 36 — “Back in 2022 it paid nine point six two percent”, the
historical trap R1 exists for — and both sentences concern the *same entity*. The extractor
sometimes emits only one claim for the pair. When it drops the live-rate sentence, a genuinely
stale video goes uncorrected, which is a product failure, not a scoring artifact.

**Cause, verified in the code, not inferred.** Neither `extract._call_model` nor
`notes._draft` passes a `temperature` to `client.messages.create`, so both run at the Anthropic
API default of 1.0. Extraction is a labelling task, not a generative one.

**Recommended fix — `temperature=0.0` in `extract._call_model`.** This is a determinism setting
on a classification call, chosen without reference to what the labels say, so it is not tuning to
the eval set. **It is deliberately NOT applied here**, for two reasons: it changes A-03's shipped
module, and proving it actually removes the flake needs several live runs, which is Zaeem's budget
to spend, not mine to assume.

**What must NOT happen.** Lowering `STALE_MATERIAL_RECALL_FLOOR` below 0.90, or moving the gate
onto a cached extraction, would turn a passing gate into a statement about nothing. The floor
stays at 0.90 and the gate stays on a live run. **The gate caught a real defect on its first
outing — that is the harness working, not the harness misbehaving.**

**Zaeem's call:** (a) pin `temperature=0.0` and re-measure, (b) treat the flake as an A-03 bug and
file it, or (c) accept an intermittently-red gate for the demo and say so out loud.

---

## D-6 — the missing `confidence` rule would not fire, and that is the problem

**Status:** AWAITING RATIFICATION · **Fix applied:** none — `judge.py` and `models.py` are both
protected from this branch. Measured here, not changed.

Verified: `grep -n confidence almanac/judge.py` returns nothing. `Claim.confidence` is required and
populated, and no rule consults it. The issue's spec R3 is `confidence < 0.6 -> unresolved`.

**Measured over 75 produced claims in one live run:**

| | |
|---|---|
| min / median / max | **0.60** / 0.90 / 0.97 |
| histogram (rounded) | 0.6 ×6 · 0.7 ×6 · 0.8 ×19 · 0.9 ×43 · 1.0 ×1 |
| claims below 0.6 | **0 of 75** |
| verdicts spec-R3 would rewrite | **0** |
| status disagreements to correlate against | **0** — there are none |
| mean confidence by status | `correct` 0.940 · `stale_material` 0.940 · `stale_immaterial` 0.945 · `skip` 0.912 · `unresolved` 0.838 |

**The finding is not "a rule is missing". It is that adding the rule as specified would change
nothing while appearing to add a safeguard.** The extractor's prompt asks only for "0 to 1: how
sure you are of this labelling" with no floor, yet the model never returns below 0.60 and puts 58
of 75 claims at 0.8–0.9. The signal is compressed into a narrow high band, so a 0.6 threshold is a
no-op *by construction* — not because extraction is reliably confident. Shipping spec-R3 as
written would buy false assurance that low-confidence extractions are handled.

Note this sits against D-5: extraction **does** drop claims between runs, and it does so while
reporting high confidence on everything it keeps. Confidence is not currently tracking the failure
mode that actually exists.

**Zaeem's call:** (a) leave `confidence` unconsulted and delete it from the spec, (b) calibrate
the prompt so the number spans a usable range before any rule reads it, or (c) implement spec-R3
knowing it is inert today and will stay inert until (b) happens. My read: **(b) before (c)** —
a threshold over an uncalibrated score is decoration.

---

## D-7 — Q2 is thinner than A-04 thought: one datum per side, not two rows

**Status:** AWAITING RATIFICATION · **Fix applied:** none. `MARKET_RATE_MATERIAL_PP` unchanged.

A-04's open question Q2 calls 0.50pp "two labelled rows … thin evidence for a global constant".
Measured, it is thinner than that. Every `market_rate` divergence in the labelled set:

| line | source | entity | claimed | delta | expected | got |
|---|---|---|---|---|---|---|
| 10 | v2 captions | `mortgage_30y_fixed` | 6.85% | +0.14pp | `stale_immaterial` | `stale_immaterial` ✓ |
| 51 | `fresh_wrong.md` | `mortgage_30y_fixed` | 6.85% | +0.14pp | `stale_immaterial` | `stale_immaterial` ✓ |
| 35 | v5 captions | `ibond_composite_rate` | 3.11% | −1.15pp | `stale_material` | `stale_material` ✓ |
| 49 | `fresh_ok.md` | `mortgage_30y_fixed` | 6.71% | +0.00pp | `correct` | `correct` ✓ |

Lines 10 and 51 are **the same number against the same catalog entry** — `mortgage_30y_fixed` at
6.85% — appearing once in a caption track and once in a script. So the immaterial side rests on
**one distinct (entity, value) pair**, not two independent observations, and the material side on
one. The constant is calibrated on one datum per side.

**All four rows classify correctly at 0.50**, so nothing here argues for moving it — and there is
no evidence anywhere in (0.14, 1.15) to place it better. **It was not changed**, per the issue's
explicit instruction. What this measurement adds is that A-04's suggested remedy — a per-key
threshold in `facts/catalog.yaml` beside `max_age_days` — cannot be evidenced from this labelled
set either: with one datum per side there is nothing to fit per key. Widening the set is the
prerequisite, not a better-placed constant.

---

## Note on the baseline comparison

A peer session's `scan --source corpus` baseline (64 verdicts) and this harness's totals (65–82)
are not directly comparable: `cli.CORPUS_CHANNEL` scopes `scan --source corpus` to
`corpus/channel` — the 5 videos — while `eval` covers every source the labels reference, which is
those 5 **plus** `corpus/scripts/fresh_ok.md` and `fresh_wrong.md`. The labels split 40 / 14 across
those trees. So "64 verdicts vs 54 labelled rows" compares a 5-video scan against a 7-source label
set; the like-for-like figure is 64 verdicts against the 40 channel labels.

---

## D-5 CORRECTION — the recommended fix does not exist on this model

**Status:** D-5's recommendation is WITHDRAWN. The finding stands; the proposed fix was wrong.
This file is append-only, so nothing above is deleted — read it with this in front of it.

Zaeem ruled "pin `temperature=0.0` in extract.py". It cannot be done, and D-5 should not have
proposed it without checking the installed SDK first — the Project Rules require verifying every
library call against the installed package before using it, and that check was skipped.

**Verified against `anthropic 1.4.0` as installed, then against the live API:**

| check | result |
|---|---|
| `temperature` in `messages.create` signature | **absent** (21 params, none is `temperature`) |
| `temperature` anywhere in `anthropic/types/` | **absent** |
| `top_p` / `top_k` | **absent** |
| `output_config` fields | `effort`, `format` only — no sampling controls |
| live call with `extra_body={"temperature": 0.0}` | **HTTP 400** — ``` `temperature` is deprecated for this model ``` (`claude-opus-5`) |

Sampling controls were removed from the Messages API for this model. There is no knob to pin.

### `output_config.effort` was tested as the nearest available lever, and does not fix it

`effort` *is* accepted for `claude-opus-5`. Measured by monkeypatch — `extract.py` was not
modified — 6 live gate runs at `effort="high"` against the standing baseline:

| | baseline | `effort="high"` |
|---|---|---|
| gate PASS | 7 / 11 runs | 5 / 6 runs |
| verdict spread | 65–79 (14) | 67–80 (13) |
| mean verdicts | 74.5 | 75.5 |
| runs dropping line 35 | every failure | every failure |

5/6 against 4/6 on the matched sub-sample is inside the noise at this n, the spread is unchanged,
and it still drops line 35. **`effort` is not the fix and is not recommended on this evidence.**

### What 17 live runs actually establish

* **`judge.py` has produced ZERO status disagreements in all 17 runs.** That result is now very
  well supported.
* The gate fails roughly a third of the time, and **every single failure is the same sentence** —
  line 35, the live i-bond composite rate, which sits immediately beside line 36's historical
  sentence about the *same entity*.

That specificity is the point. This is not diffuse sampling noise that a temperature knob would
have flattened; it is one sentence being intermittently merged into its neighbour. It is the same
failure family as **D-2**, where the extractor emits one claim covering two labelled rows.

### Revised recommendation — a targeted prompt change, NOT applied

Instruct the extractor to emit one claim per number, and specifically not to collapse a
present-tense value into an adjacent historical sentence about the same entity. That is a
`extract.py` prompt change, which the issue does put in scope ("you fix the prompt or the rule").

**It is not applied here.** The last confident recommendation in this ADR was wrong because it
was not verified first; this one changes extraction semantics, would interact with D-2 and D-3,
and needs Zaeem's call plus a re-measured 6-run gate before it is believed.
