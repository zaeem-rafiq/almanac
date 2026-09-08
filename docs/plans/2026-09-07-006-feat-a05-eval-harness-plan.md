# A-05 · Eval harness — plan

Issue HAC-41 · Milestone M2 · budget 1.5h / 35 turns · **never cut**.

## What ships

`python -m almanac eval` runs extract + judge over every source referenced in
`evals/claims.jsonl`, matches each labelled row to a produced verdict by normalised quote
overlap, and reports (a) extraction recall, (b) `claim_type` accuracy, (c) a per-status
precision/recall table, (d) a hard-negative line, (e) every miss with `expected` vs `got` and
`rule_fired`. Writes `evals/results.md` and `evals/results.json`. Exits non-zero if
hard-negative false positives > 0 or `stale_material` recall < 0.90.

## The rule that governs this issue

**Nothing is tuned to make the table green.** `evals/claims.jsonl` is Zaeem's; this branch does
not write to it. `MARKET_RATE_MATERIAL_PP` is not touched. Misses are *presented*, not fixed.
Any change to a rule or a prompt that survives the review is recorded in
`docs/decisions/ADR-002-eval-loop.md` with the reason.

## Verified state (not assumed)

| Thing | Checked | Result |
|---|---|---|
| Baseline suite | `venv/bin/python -m pytest tests/ -q` | 201 passed |
| `rule_fired` shape | read `almanac/judge.py` | always `R<n>.<name>` — `R1.historical_matches_window`, `R2.unjudged_claim_type`, `R3.no_entity_key`, `R4.matches_current`, `R5.stale_material`, `R5.stale_immaterial`, … Per-rule reporting keys on the `R<n>` prefix, which is stable across the issue-text renumbering recorded in `docs/proofs/A-04.md`. |
| Label schema | read `evals/claims.jsonl` | 54 rows; fields `source`, `quote`, `expected_claim_type`, `expected_entity_key`, `expected_status`. There is **no** `claim_type` field — it is `expected_claim_type`. |
| `structural` | 4 rows, all `expected_status: skip`, all `expected_entity_key: null` | a first-class value in the accuracy metric, never "unknown" |
| Hard-negative set | 27 `illustrative` + 1 `historical` | **28 rows, all `expected_status: skip`** — a real adversarial set |
| `.env` | present in the main checkout, absent in this worktree | `load_env()` resolves `REPO_ROOT/.env` = the worktree, so the key is passed through the process environment. `.env*` is protected; this branch does not create one. |

## Design

**Matching — normalised quote overlap.** Both the labelled quote and each produced claim's quote
are located in the source's *normalised* text (`extract._normalise`: whitespace collapsed, curly
quotes straightened — the same leniency extraction already allows and nothing looser). Match
strength is intersection-over-union of the two character spans. Assignment is greedy by
descending IoU and one-to-one, so two labels can never claim the same verdict.

`scripts/score_a04.py` matched with `text.find(candidate.quote)` and took the *first* overlapping
candidate. That finds the first textual occurrence rather than the claim's own span, and takes an
arbitrary candidate rather than the best one. The harness does not inherit either behaviour.

**The threshold is reported, not tuned.** `MIN_QUOTE_IOU = 0.30`. The run prints the matched-IoU
distribution and a sensitivity row (matched count at 0.20 / 0.30 / 0.50). If the count moves
across that sweep the threshold is load-bearing and must be argued in the ADR; if it does not,
the threshold is not doing the work.

**Liveness before negatives.** A "0 false positives" gate passes trivially on an empty run. The
harness asserts, in order: sources read > 0 → verdicts produced > 0 → rows matched > 0 → *then*
the hard-negative count. Every printed metric carries its denominator.

**Determinism.** `docs/proofs/A-04.md` records 56–67 verdicts across otherwise identical runs, so
extraction is materially nondeterministic. `--cache PATH` persists one extraction so that a
re-run during the miss review scores the *same* claims and any change is attributable to the
label or the rule, not to resampling. Off by default. The gate run is a LIVE extraction that also seeds the cache, so the reviewed misses and the gated numbers are the same run.

## Steps, each with its check

1. `almanac/evaluate.py` — loader, matcher, metrics, gate. Pure; no I/O beyond reading labels.
   → `pytest tests/test_eval.py -q` (offline, hand-built claims — no model).
2. `eval` subcommand in `almanac/cli.py` (`--cache`, `--out`, `--min-iou`).
   → `python -m almanac eval --help` and a live run.
3. `tests/test_eval.py` — matcher one-to-one, IoU floor, metric arithmetic on a fixture where
   the answers are known by hand, gate fires on a seeded false positive, gate fires on empty
   input rather than passing vacuously.
   → `pytest tests/test_eval.py -q`
4. Live gate run → `evals/results.md` + `evals/results.json`.
   → `python -m almanac eval`; echo `$?`
5. Proof 3 check: every rule R1–R5 has at least one dedicated test with at least one assertion.
   → an AST count over `tests/test_judge.py`, printed per rule.
6. **Stop. Present the misses to Zaeem.** Quote, expected vs got, `rule_fired`, and a one-line
   read of which side is wrong. No label edits, no threshold edits.
7. Record his decisions in `docs/decisions/ADR-002-eval-loop.md`; apply only what he approves;
   re-run; append proofs; walkthrough.

## Bound

1.5h / 35 turns. On the bound: `docs/blockers/A-05.md`, print `BLOCKED A-05`, stop.
