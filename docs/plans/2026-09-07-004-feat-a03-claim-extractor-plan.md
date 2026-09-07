# A-03 (HAC-39) — Claim extractor

- Issue: HAC-39 · Milestone M1 · Budget 1.5h / 40 turns
- Pre-conditions: A-01 PASS, A-02 PASS. Baseline `venv/bin/pytest tests/ -q` → **50 passed**.
- Protected this issue: `facts/catalog.yaml`, `evals/claims.jsonl`, `corpus/**`.
  `almanac/models.py` becomes protected *after* this issue — the `Claim` shape must be right now.

## 1. The principle, restated as a build constraint

**The LLM reads, the code decides.** In this issue that has three concrete consequences, and
each one is a line of code rather than a slogan:

| Decision | Who makes it | Where |
|---|---|---|
| "what kind of sentence is this" | the model | `extract.py` tool call |
| "which catalog key does this name" | the model **proposes**, the code **admits** | `_admit_entity_key()` — anything outside `load_catalog()` becomes `None` |
| "where in the source did this come from" | the code, always | `_locate()` maps the quote back to a char offset, and the offset picks the locator |
| "is this quote real" | the code | `_locate()` returns `None` → the claim is **dropped and counted** |
| "is this number right / stale / material" | **nobody, not here** | that is `judge.py` (A-04) |

The prompt therefore contains no verdict vocabulary — no "stale", "correct", "material",
"out of date", "should be updated". A prompt that asks the model whether a number is current is
wrong even when its output looks right, because it moves the decision into the unauditable half
of the system.

## 2. `Claim` — the shape (models.py, protected after this issue)

```
source_id   str          e.g. "corpus/channel/v1-401k-limits-explained/captions.srt"
locator     str          SRT start timestamp "00:00:15,000", or script line "L7"
quote       str          <=200 chars, VERBATIM substring of the source text
claim_type  Literal      statutory_limit|tax_bracket|market_rate|illustrative|historical|other
entity_key  str|None     restricted to facts/catalog.yaml keys, read at runtime
value       float|None
unit        Literal|None usd|pct
year_hint   int|None
confidence  float        0..1
```

`source_id` follows the `evals/claims.jsonl` convention: a repo-relative path.

`source_id` and `locator` are **not** in the model-facing schema. The model returns
(`quote`, `claim_type`, `entity_key`, `value`, `unit`, `year_hint`, `confidence`); provenance is
assigned by code. A model cannot invent a locator it is never asked for.

## 3. Verbatim, without loosening

`_locate(text, quote)` normalises **whitespace runs → single space** and **curly quotes → straight**
on both sides while building a normalised-index → original-index map, finds the normalised quote
in the normalised text, then returns the **original** slice `text[start:end]`.

The stored `quote` is that recovered original slice, so `claim.quote in text` is literally true by
construction rather than by assertion. No fuzzy match, no ratio threshold: a hallucinated quote
fails `str.find` and the claim is dropped. Drops are counted and reported, never silent.

Over-long quotes are trimmed to the last word boundary within 200 chars — a prefix of a verbatim
substring is still a verbatim substring.

## 4. Chunking and concurrency

- Sources are read into one document plus a `list[Locator]` of `(start, end, label)` char spans.
  SRT cue texts join with a single space — matching the quote convention already in
  `evals/claims.jsonl`, whose quotes cross cue boundaries.
- Chunks are cut on **locator boundaries** at ~2,000 tokens (~8,000 chars, 4 chars/token), and each
  chunk carries its base offset. That is what "locators preserved" means mechanically: a quote
  found at chunk-local offset `i` maps to global `base + i`, which selects the locator.
- One `ThreadPoolExecutor(max_workers=4)` runs the chunks. `extract_sources()` flattens chunks
  from *all* sources into that same pool, so the 5-video proof run is 5 concurrent calls, not 5
  sequential ones — this is what buys the <90s wall-time budget.

## 5. Call shape (ADR-000 §4, G-3 RESOLVED — no schema flattening)

```
client.messages.create(model=LLM_MODEL, max_tokens=4096,
    system=<prompt>,
    tools=[{"name":"record_claims","description":...,"input_schema":_Extraction.model_json_schema()}],
    tool_choice={"type":"tool","name":"record_claims"},
    messages=[{"role":"user","content":<chunk>}])
-> [b for b in msg.content if getattr(b,"type",None)=="tool_use"] -> _Extraction.model_validate(b.input)
```

Proven live against `claude-opus-5` in `scripts/preflight.py::check_llm`.

## 6. Steps, each with its check

| # | Step | Check |
|---|---|---|
| 1 | `Claim` + `ClaimType` in `models.py` | `venv/bin/pytest tests/test_extract.py -q` |
| 2 | `extract.py`: readers, `_locate`, chunker, `_admit_entity_key`, prompt, `extract()`, `extract_sources()` | offline unit tests, no network |
| 3 | `extract` subcommand in `cli.py` | `venv/bin/python -m almanac extract corpus/channel/v1-401k-limits-explained` prints a table |
| 4 | `scripts/proof_a03.py` | prints 4 PROOF lines, appends to `docs/proofs/A-03.md` |
| 5 | regression | `venv/bin/pytest tests/ -q` ≥ 50 passed |

## 7. Proofs, and the two traps A-00/A-01 already paid for

1. `V1 yields >=1 claim with entity_key=k401_employee_deferral, claim_type=statutory_limit, value matches the script`
2. `V4 yields 0 claims with claim_type in {statutory_limit, tax_bracket, market_rate}`
   — **negative assertions prove nothing alone.** This proof additionally asserts V4 returned a
   non-empty claim list, and that the same run produced those three types on *other* videos. An
   extractor that silently returned `[]` fails this proof instead of passing it.
3. `V5 "9.62%" claim has claim_type=historical`
4. `every returned entity_key is in catalog or None; every quote is verbatim; 5-video wall time <90s`
   — **assert the size of what was checked.** The proof counts claims, counts entity-key checks,
   counts quote checks, and asserts all three counts are equal and non-zero. A claim that never got
   checked can then no longer look identical to one that passed.

## 8. Known risks

- Every number in the captions is **spelled out in words** ("twenty three thousand dollars"). The
  model must emit `23000.0` for `value` while quoting the words verbatim. Proof 1 tests exactly this.
- Non-determinism: the proofs assert *presence* of specific labelled claims, not an exact claim
  count, so a run that finds one extra `illustrative` sentence still passes.

## 9. Finding raised, not fixed

`evals/claims.jsonl` uses `expected_claim_type: "structural"` on 4 rows. `structural` is not in the
A-03 `claim_type` vocabulary. The corpus and labels are protected, so this is reported to Zaeem, not
edited. It does not block A-03 (those 4 rows land as `other`), but A-04's judge must reconcile it.
