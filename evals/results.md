# Almanac eval — extract + judge vs. the labelled set

`python -m almanac eval` · 2026-09-08 01:53 UTC

**GATE: PASS**

## Run

| | |
|---|---|
| sources read | 7 |
| verdicts produced | 111 |
| labelled rows | 54 |
| **rows matched** | **46 / 54** |
| labelled rows with no claim | 8 |
| labelled quotes absent from their source | 0 |
| verdicts with no labelled row | 65 |
| extraction | live |

Matching: a verdict's quote covers ≥ 0.90 of the labelled quote, greedy and one-to-one, ties broken by the tighter quote.

Coverage-floor sensitivity (matched rows): ≥0.50 → 52 · ≥0.70 → 48 · ≥0.90 → 46 · ≥1.00 → 46.
**The count moves inside the operating band (≥ 0.70) — the floor is load-bearing and the choice belongs in the ADR.**

## (a) Extraction recall

**85%** — 46 of 54 labelled claims were found by `extract.py`.

## (b) claim_type accuracy

**98%** — 45 of 46 matched rows carry the labelled type.

| expected \ got | `statutory_limit` | `market_rate` | `illustrative` | `historical` | `structural` | `other` | total |
|---|---|---|---|---|---|---|---|
| `statutory_limit` | 18 | · | · | · | · | · | 18 |
| `market_rate` | · | 4 | · | · | · | · | 4 |
| `illustrative` | · | · | 19 | · | · | 1 | 20 |
| `historical` | · | · | · | 1 | · | · | 1 |
| `structural` | · | · | · | · | 3 | · | 3 |
| `other` | · | · | · | · | · | · | 0 |

## (c) Per-status precision / recall

Recall counts **every labelled row of that status**, including rows extraction never produced a claim for — a missed stale sentence is a video that stays wrong. Precision is over matched rows only, since an unmatched verdict has no label to be scored against.

| status | precision | recall | TP | FP | FN | support | of which not extracted |
|---|---:|---:|---:|---:|---:|---:|---:|
| `skip` | 1.00 | 0.75 | 24 | 0 | 8 | 32 | 8 |
| `correct` | 1.00 | 1.00 | 9 | 0 | 0 | 9 | 0 |
| `stale_material` | 1.00 | 1.00 | 7 | 0 | 0 | 7 | 0 |
| `stale_immaterial` | 1.00 | 1.00 | 2 | 0 | 0 | 2 | 0 |
| `unresolved` | 1.00 | 1.00 | 4 | 0 | 0 | 4 | 0 |

| expected \ got | `skip` | `correct` | `stale_material` | `stale_immaterial` | `unresolved` | `<not extracted>` |
|---|---|---|---|---|---|---|
| `skip` | 24 | · | · | · | · | 8 |
| `correct` | · | 9 | · | · | · | · |
| `stale_material` | · | · | 7 | · | · | · |
| `stale_immaterial` | · | · | · | 2 | · | · |
| `unresolved` | · | · | · | · | 4 | · |

## (d) Hard negatives

**0 illustrative/historical row(s) wrongly flagged as stale** — out of 21 matched (of 28 labelled hard negatives). Ceiling 0.

Stale verdicts outside the labelled set: 0 (not gated — the issue defines the hard-negative count over labelled rows — but a rising number means the eval set has stopped covering what the pipeline emits).

## (e) Misses

0 status disagreement(s) on matched rows, plus 8 labelled row(s) extraction produced no claim for.

Labelled rows with no claim produced:

| line | expected | claim_type | quote |
|---|---|---|---|
| 7 | `skip` | `illustrative` | Six percent is four thousand two hundred dollars of your own money |
| 25 | `skip` | `illustrative` | Ten thousand dollars. It showed up, it is yours |
| 29 | `skip` | `illustrative` | Whatever is left goes into three funds. Not thirty. Three. |
| 30 | `skip` | `illustrative` | Say you go sixty forty, sixty percent stocks and forty percent bonds |
| 31 | `skip` | `illustrative` | Or eighty twenty if you are younger |
| 33 | `skip` | `illustrative` | Over thirty years, at that assumed rate, a lump sum roughly doubles a… |
| 39 | `skip` | `structural` | If you cash out before five years you give up three months of interest |
| 52 | `skip` | `illustrative` | Say you make $50,000 and your plan matches the first 5% |

### claim_type disagreements (status may still agree)

| line | expected | got | status | quote |
|---|---|---|---|---|
| 15 | `illustrative` | `other` | `skip` | Fifteen years of extra payments applied to the wrong bucket |
