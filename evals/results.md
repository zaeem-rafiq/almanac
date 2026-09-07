# Almanac eval — extract + judge vs. the labelled set

`python -m almanac eval` · 2026-09-07 22:01 UTC

**GATE: PASS**

## Run

| | |
|---|---|
| sources read | 7 |
| verdicts produced | 77 |
| labelled rows | 54 |
| **rows matched** | **48 / 54** |
| labelled rows with no claim | 6 |
| labelled quotes absent from their source | 0 |
| verdicts with no labelled row | 29 |
| extraction | live |

Matching: a verdict's quote covers ≥ 0.90 of the labelled quote, greedy and one-to-one, ties broken by the tighter quote.

Coverage-floor sensitivity (matched rows): ≥0.50 → 49 · ≥0.70 → 48 · ≥0.90 → 48 · ≥1.00 → 48.
Constant at 48 across the operating band (≥ 0.70), so the floor is not doing the work. The sub-band probe at ≥ 0.50 returns 49, which shows the sweep can move — a constant band is evidence, not a metric that never varies.

## (a) Extraction recall

**89%** — 48 of 54 labelled claims were found by `extract.py`.

## (b) claim_type accuracy

**98%** — 47 of 48 matched rows carry the labelled type.

| expected \ got | `statutory_limit` | `market_rate` | `illustrative` | `historical` | `structural` | total |
|---|---|---|---|---|---|---|
| `statutory_limit` | 18 | · | · | · | · | 18 |
| `market_rate` | · | 4 | · | · | · | 4 |
| `illustrative` | · | · | 20 | · | 1 | 21 |
| `historical` | · | · | · | 1 | · | 1 |
| `structural` | · | · | · | · | 4 | 4 |

## (c) Per-status precision / recall

Recall counts **every labelled row of that status**, including rows extraction never produced a claim for — a missed stale sentence is a video that stays wrong. Precision is over matched rows only, since an unmatched verdict has no label to be scored against.

| status | precision | recall | TP | FP | FN | support | of which not extracted |
|---|---:|---:|---:|---:|---:|---:|---:|
| `skip` | 1.00 | 0.81 | 26 | 0 | 6 | 32 | 6 |
| `correct` | 1.00 | 1.00 | 9 | 0 | 0 | 9 | 0 |
| `stale_material` | 1.00 | 1.00 | 7 | 0 | 0 | 7 | 0 |
| `stale_immaterial` | 1.00 | 1.00 | 2 | 0 | 0 | 2 | 0 |
| `unresolved` | 1.00 | 1.00 | 4 | 0 | 0 | 4 | 0 |

| expected \ got | `skip` | `correct` | `stale_material` | `stale_immaterial` | `unresolved` | `<not extracted>` |
|---|---|---|---|---|---|---|
| `skip` | 26 | · | · | · | · | 6 |
| `correct` | · | 9 | · | · | · | · |
| `stale_material` | · | · | 7 | · | · | · |
| `stale_immaterial` | · | · | · | 2 | · | · |
| `unresolved` | · | · | · | · | 4 | · |

## (d) Hard negatives

**0 illustrative/historical row(s) wrongly flagged as stale** — out of 22 matched (of 28 labelled hard negatives). Ceiling 0.

Stale verdicts outside the labelled set: 0 (not gated — the issue defines the hard-negative count over labelled rows — but a rising number means the eval set has stopped covering what the pipeline emits).

## (e) Misses

0 status disagreement(s) on matched rows, plus 6 labelled row(s) extraction produced no claim for.

Labelled rows with no claim produced:

| line | expected | claim_type | quote |
|---|---|---|---|
| 5 | `skip` | `illustrative` | Say you make seventy thousand dollars |
| 12 | `skip` | `illustrative` | Say you have three hundred thousand dollars left on the loan |
| 14 | `skip` | `illustrative` | an extra five hundred dollars a month you could throw at either side |
| 25 | `skip` | `illustrative` | Ten thousand dollars. It showed up, it is yours |
| 27 | `skip` | `illustrative` | you have no debt above eight percent |
| 29 | `skip` | `illustrative` | Whatever is left goes into three funds. Not thirty. Three. |

### claim_type disagreements (status may still agree)

| line | expected | got | status | quote |
|---|---|---|---|---|
| 23 | `illustrative` | `structural` | `skip` | you can pull that money out of the account tax free in twen… |
