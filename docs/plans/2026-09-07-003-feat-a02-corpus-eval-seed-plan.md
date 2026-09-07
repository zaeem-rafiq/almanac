---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
title: "feat: A-02 synthetic channel corpus + Zaeem-labeled claims"
created: 2026-09-07
issue: A-02 (HAC-38) — https://linear.app/tatheer17/issue/HAC-38
milestone: M1 Engine, local
budget: 1.5h / 35 turns
depends_on: A-00 (HAC-36) PASS, A-01 (HAC-37) PASS
---

# feat: A-02 · Synthetic channel corpus + Zaeem-labeled claims

## Goal Capsule

Give Almanac something to be evaluated against: five synthetic finance-YouTube videos with timed
captions, two loose scripts, and a hand-labeled claim set (`evals/claims.jsonl`) that names the
expected verdict for every number. A-03's extractor and A-05's judge are graded against this file,
so a mislabeled row here is a permanently wrong grade downstream.

The load-bearing constraint, inherited from A-01: **the agent invents no number.** Every figure
spoken in the corpus is either a `history` value in `facts/catalog.yaml`, a current catalog value,
or a figure quoted verbatim from a source page that already serves this project.

---

## Problem Frame

A-01 decided what "true" means. A-02 decides what "asked" means — and, crucially, what should
*not* be asked. Almanac's credibility rests less on catching a stale 401(k) limit than on staying
silent about "say you earn $80,000". A corpus with no hard negatives would grade a flag-everything
extractor as perfect.

### Three gaps in the issue text, resolved before writing (Zaeem approved 2026-09-07)

The rule "numbers in the scripts come from `history` values in the catalog" collides with the
required content mix, because A-01 populated exactly one `history` row per manual key (2025) and
zero for the rates keys. Three script numbers had no sourced home:

| video | needed | catalog had | resolution |
|---|---|---|---|
| V1, dated 2024-02 | 2024 401(k) deferral | 2026, 2025 | fetched from the **already-cited** irs.gov COLA page, quoted, approved, transcribed |
| V5, dated 2024-11 | I-bond composite for the Nov-24 window | May-26, May-25 | fetched from the **already-cited** treasurydirect.gov rate chart, same convention |
| V2, dated 2025-06 | 30-year mortgage in Jun-2025 | `history: []` — rates keys carry none | fetched from Freddie Mac `PMMS_history.csv`, the same feed `refresh_rates()` reads |

Each followed A-01's discipline exactly: fetch a 200-serving page, quote it verbatim into the
transcript, get Zaeem's sign-off, *then* transcribe. The agent filled nothing from memory.

### One spec correction

HAC-38 says V2 is judged "depending on today's FRED value". **FRED is used nowhere in Almanac**
(ADR-000 §9); the 30-year rate comes from Freddie Mac PMMS. Confirmed with Zaeem: PMMS
(`mortgage_30y_fixed` = 6.71 @ 2026-09-03) is the comparison basis. Linear text left to Zaeem.

---

## Requirements

R1. `corpus/channel/` holds five video folders, each with `meta.json`
    (`video_id`, `title`, `published_at`, `description`) and `captions.srt`.
R2. Each `captions.srt` parses with the installed `srt` library and holds 500–800 words.
R3. Content mix is exactly the issue's: V1 stale-material 401(k); V2 mortgage vs invest;
    V3 stale-material HSA; V4 all-illustrative (every claim skipped); V5 I-bond + a historical
    reference that must be skipped.
R4. `corpus/scripts/fresh_ok.md` (all current) and `fresh_wrong.md` (one wrong IRA limit, one
    stale mortgage rate, two illustrative examples) replace the A-00 placeholders.
R5. `evals/claims.jsonl` ≥40 rows of
    `{source, quote, expected_claim_type, expected_entity_key|null, expected_status}`,
    covering all five statuses, with ≥6 hard negatives.
R6. Every non-null `expected_entity_key` exists in `facts/catalog.yaml`.
R7. No real creator name, ticker, or brand URL anywhere in `corpus/`.

## Key Technical Decisions

**D1. The corpus is generated, not hand-typed.** `scripts/build_corpus.py` holds the prose and
emits `meta.json` + `captions.srt` via `srt.compose`. Hand-typed timestamps drift and overlap;
composing them makes monotonicity structural. The proof re-parses the files from disk with
`srt.parse`, so it tests the artifact, not the generator's intent.

**D2. The brand/ticker check is an allowlist, not a wordlist.** A-01's lesson: a negative
assertion proves nothing unless the check can demonstrably fail. Grepping for a list of known
creator names passes trivially — it only ever finds what someone thought to list. Instead the
detector *inverts the burden*: it extracts every capitalised proper noun, cashtag, @handle and
domain in the corpus and fails on anything not in an explicit allowlist of generic finance terms.
An unknown proper noun is a failure by default.

**D3. The check is proven by a control fixture.** Before asserting the corpus is clean, the same
detector runs against a planted string containing a real creator name, a real cashtag, a brand
URL and an @handle, and must flag all four. A clean run only counts after the dirty run fails.

**D4. `unresolved` rows use real but deliberately uncatalogued IRS figures** (HCE threshold,
defined-contribution limit, 457 deferrals, key-employee limit) read off the same COLA page. This
keeps "no invented numbers" true even for the rows Almanac is supposed to give up on.

**D5. `history` on a `source: rates` key is hand-maintained.** `refresh_rates()` writes
`facts/rates.json` only and never touches `catalog.yaml`, so a `history` block on
`mortgage_30y_fixed` is stable across refreshes. Verified by inspection before editing.

## Implementation Units

### U1. Three approved `history` rows → `facts/catalog.yaml`
`k401_employee_deferral` +2024/23000; `ibond_composite_rate` +Nov-24/3.11;
`mortgage_30y_fixed` +Jun-2025/6.85. Targeted text insertion, not a YAML round-trip — the file's
comment block is load-bearing documentation and `yaml.dump` would erase it.
*Check:* `pytest tests/test_catalog.py -q` still 17 passed; `python -m almanac facts` still 15 keys.

### U2. `scripts/build_corpus.py` → five videos
Prose written to sound like the format (cold-open hook, chaptered body, "not financial advice"
sign-off), 500–800 words, no named person.
*Check:* `python scripts/build_corpus.py` then the A-02 proof script.

### U3. `corpus/scripts/fresh_ok.md`, `fresh_wrong.md`
*Check:* referenced by `evals/claims.jsonl` rows and covered by R4's content requirement.

### U4. `evals/claims.jsonl` — agent DRAFTS, Zaeem LABELS
Every row's `expected_*` fields are Zaeem's to confirm or edit. The file is protected from the
moment he finishes the pass.
*Check:* proof 2.

### U5. `scripts/check_corpus.py` + `tests/test_corpus.py`
Three proofs as functions, run both from the CLI (transcript output) and from pytest (regression).
*Check:* `pytest tests/ -q` green, and the control fixture fails as designed.

## Verification Contract

```
PROOF A-02: 5 videos, each captions.srt parses with srt lib, 500-800 words, meta.json valid = PASS
PROOF A-02: evals/claims.jsonl has >=40 rows, all 5 statuses present, >=6 hard negatives, every expected_entity_key exists in catalog = PASS
PROOF A-02: grep shows no real creator names, tickers, or brand URLs in corpus = PASS
```

Proof 3 is reported honestly: the mechanism is an allowlist scan plus a control fixture, which is
strictly stronger than the "grep" the issue names. The proof line keeps the issue's wording; the
walkthrough records what actually ran.

## Definition of Done

All three PROOF lines PASS, printed to the transcript and appended to `docs/proofs/A-02.md`;
`pytest tests/ -q` green including the new corpus tests; walkthrough written; committed and pushed.

## Risks

| risk | mitigation |
|---|---|
| Labels encode the agent's reading of the rules rather than Zaeem's | he confirms or edits every `expected_*` field; file protected afterwards |
| Allowlist detector is so strict the corpus can't be written | allowlist is explicit and reviewable; every entry is a generic finance term, not a brand |
| Word-count window missed after an edit | counted in the proof, not by eye |
| A-05 grades against a label that later proves wrong | `docs/proofs/A-02.md` is append-only; a correction is appended, never edited over |
