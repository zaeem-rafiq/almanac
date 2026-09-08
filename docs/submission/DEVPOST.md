# Devpost submission — Almanac

**Status: PREPARED, NOT SUBMITTED.** Every field below is ready to paste. Zaeem submits.
Claude does not create the Devpost account, accept its terms, or press Submit.

Prepared 2026-09-07, re-verified against `main` @ `89085f7` after the A-09 deploy landed.
Every figure here was re-derived from the repo, and every live-URL figure was re-run against the
deployed host in this session. Provenance for each one is in
[`docs/proofs/A-12.md`](../proofs/A-12.md).

> **Two tags in the Linear issue (HAC-48) are factually wrong and are NOT used below.**
> The issue was written before the engine migration.
> * **"Anthropic" → Google Vertex AI / Gemini.** `grep -rn "anthropic" --include="*.py" almanac/ web/`
>   returns nothing; `almanac/extract.py:438 _client()` builds `genai.Client(vertexai=True, …)`
>   against `gemini-3.8-flash`. Shipping "Anthropic" would be exactly the stale claim this
>   product exists to catch.
> * **"FRED" → Treasury / BLS / Freddie Mac / NY Fed.** There is no FRED call anywhere in the
>   codebase. The four market rates in `facts/rates.json` carry `fetched_via` values
>   `bls`, `nyfed`, `freddiemac`, `treasury`.

---

## Fields to fill, in Devpost's order

### 1. Project name
```
Almanac
```

### 2. Tagline / elevator pitch
```
Every gate in this gallery checks the video you're about to publish. Almanac checks the ones you already did.
```

### 3. "Built with" tags
Enter these one at a time. Devpost lowercases and hyphenates as you type.

```
python
fastapi
google-cloud
vertex-ai
gemini
youtube-api
github-actions
vercel
pydantic
pytest
playwright
```

Do **not** enter `anthropic` or `fred`. Neither is in the codebase.

### 4. Links

| Field | Value |
|---|---|
| **Repository** | `https://github.com/zaeem-rafiq/almanac` — verified PUBLIC |
| **Try it out / live URL** | `https://almanac-gamma.vercel.app` — verified 200 this session. Use this stable alias, **not** the per-deploy `almanac-<hash>-zk-hackathon.vercel.app`, which changes on every deploy. |
| **Video demo** | **NONE YET.** Optional per the brief. Leave blank rather than linking a placeholder. |

### 5. Gallery images
Five real screenshots in `docs/submission/` — four of the app running locally, one of the
deployed host. Upload in this order; the first becomes the project thumbnail.

| # | File | What it shows |
|---|---|---|
| 1 | `01-verdict-row.png` | One verdict: the quote, the catalog key, said $23,000 vs current $24,500 from irs.gov, the −$1,500 delta, `R5.stale_material`, and the drafted 📌 Update note |
| 2 | `02-eval-scorecard.png` | "What this page is allowed to claim" — the eval numbers, next to the caveats pane that says what they do not prove |
| 3 | `03-catalog-watch.png` | The whole back-catalogue scan: 5 videos, 6 stale_material / 1 stale_immaterial / 1 correct / 3 unresolved / 72 skip |
| 4 | `04-live-lint.png` | A live end-to-end run in the browser: 12 numbers read in 44.4s, one stale_material, one stale_immaterial |
| 5 | `05-live-site.png` | The same surface on the **deployed** host, captured from `https://almanac-gamma.vercel.app` — 11 verdict rows, 0 console errors |

**The "Actions run log" image the issue asks for still does not exist and was not faked.**
`gh run list` returns zero rows — no workflow has ever executed. This is no longer because the
workflows are stubs: A-09 merged, `keepalive.yml` now carries a real `cron: "*/10 12-22 8 9 *"`
and the repo variable `ALMANAC_URL` is set. Nothing has simply fired yet. Images 4 and 5 are the
substitutes, and image 5 is the stronger one: it is the deployed host, not a local server.

---

## 6. Project description — paste this whole block

> Devpost's description field takes Markdown.

### The problem

A finance creator's back catalogue keeps earning views and keeps giving 2023's numbers. The
401(k) deferral limit moved. The I-bond rate moved. The video did not. Nobody re-watches 200
old uploads to find the four sentences that went wrong, so the wrong number keeps teaching.

Every other tool in this category checks the video you are about to publish. Almanac checks
the ones you already did.

### What it does

Almanac reads every number in a creator's scripts and captions, resolves each one against a
sourced facts table, and drafts the "📌 Update" pin for the videos that went stale.

* **15 facts.** 10 statutory limits and 5 market rates. 11 are entered by hand and each carries
  a primary-source URL on `irs.gov` or `treasurydirect.gov`; tests fail on a null value or a URL
  that does not return 200. The other 4 are the market rates, refreshed from Treasury, BLS,
  Freddie Mac and the New York Fed.
* **Every entity carries history**, which is what lets an accurate sentence about 2022 survive.
* **YouTube is read over the owner's own OAuth** (Data API v3). No scraping, no
  `youtube-transcript-api`, no `yt-dlp`.
* **Writing back is guarded three ways.** `videos.update` runs only when `ALMANAC_WRITE=true`
  **and** the target channel equals `ALMANAC_TEST_CHANNEL_ID` **and** the CLI was given
  `--apply`. Default is a dry-run diff. The test suite drives the real counter and asserts it
  stays at zero for every other combination.

### How it decides

**The LLM reads. The code decides.**

`extract.py` and `notes.py` are the only two modules that may call a model, and neither one
produces a verdict. The extraction prompt contains no verdict vocabulary — it never asks
whether a number is current, right, or worth changing. Every verdict comes from `judge.py`,
which calls no model, and every verdict carries the `rule_fired` that produced it.

The rules run in this order. The ordering is the design, not an implementation detail:

| # | `rule_fired` | What it does | Why it is where it is |
|---|---|---|---|
| **R1** | `R1.historical_window_missing`<br>`R1.historical_matches_window`<br>`R1.historical_disputed` | A claim the speaker placed in the past resolves against the catalog's **history**, never against today | Runs first so it can stop R5 firing on an accurate sentence. *"Back in 2022 it paid nine point six two percent"* sits 125.8% from today's I-bond rate — the loudest divergence in the corpus, and completely true. R1 finds the 2022 window, matches 9.62, returns `skip`. No covering window is treated as **missing evidence** (`unresolved`), not as a contradiction. |
| **R2** | `R2.unjudged_claim_type` | `illustrative` / `structural` / `other` / unkeyed `historical` → `skip` | *"Say you make seventy thousand dollars"* is a teaching example. It is not checkable and must never be corrected. |
| **R3** | `R3.no_entity_key`<br>`R3.no_claimed_value`<br>`R3.no_fact_value` | A judged claim the catalog cannot resolve → `unresolved` | Common and normal, not a failure. Saying "I don't know" is a first-class outcome. |
| **R4** | `R4.matches_current` | `claimed == current` → `correct` | Exact. All nine labelled `correct` rows differ by 0.00, so the code carries no tolerance band it did not earn. |
| **R5** | `R5.stale_material`<br>`R5.stale_immaterial` | `claimed != current` → stale, material **by kind** | The only rule that can call something stale. |

Materiality is per-kind because a single threshold does not reproduce the labels. Across the
labelled set `stale_material` spans −27.0%..−2.17% and `stale_immaterial` sits at +2.09%: the
bands **overlap**, 0.08 percentage points apart. What separates them is the domain, read from
`facts/catalog.yaml` and not from the model:

* `statutory_limit` / `tax_bracket` — an exact-match domain. Any difference is material. A
  viewer who contributes $23,000 when the limit is $24,500 contributed the wrong amount, and
  the size of the gap does not change that.
* `market_rate` — a drift domain. Rates move constantly and the video's argument survives a
  small move. The bar is `MARKET_RATE_MATERIAL_PP = 0.50`.

*(The rules were renumbered from the original spec during A-04; the values above are the ones
`judge.py` actually emits.)*

### Proof

Scored against 54 human-labelled claims across 5 caption files and 2 scripts.
Artifact: `evals/results.json`. Gate: **PASS**.

| Measure | Result |
|---|---|
| `stale_material` precision / recall | **1.00 / 1.00** (7 of 7) |
| Hard-negative false positives | **0** — of 28 labelled illustrative/historical rows, 21 matched, none corrected |
| Status disagreements on matched rows | **0**, across all five statuses |
| `correct` / `stale_immaterial` / `unresolved` | 1.00 / 1.00 each |
| Extraction recall | **85%** (46 of 54) |
| `claim_type` accuracy | **98%** (45 of 46) |
| `skip` recall | 0.75 (24 of 32) — the 8 misses are all rows extraction never produced a claim for |
| Verdicts produced | 111 |
| Test suite | **290 passing** |

**Reproducibility, measured rather than promised.** 9 `almanac eval` runs, live extraction, each
to its own output directory: **9/9 gate PASS**. `temperature=0.0` and a pinned `seed`, both
available on Vertex, make the run reproducible. The 9 runs sit in **two blocks**, keyed on the
SHA-256 of `almanac/extract.py`, because reproducibility is a property of that file: 6 runs
before the same-sentence guard and 3 after it. Output is **byte-identical within each block**.
The blocks are deliberately **not** pooled — the guard changed what counts as a claim, so verdicts
went 115 → 111 while `matched` held at 46 of 54.

### What's honest

This section is the point of the project, so it is not the short one.

1. **Reproducibility is an observation, not a guarantee.** One corpus, one region, one model
   version. Google does not promise determinism for a pinned temperature and seed, and a model
   revision could change it without notice. What the measurement rules out is the defect this
   project recorded on its previous engine, where verdict counts moved 65–84 between runs and
   the gate failed 2 runs in 5.
2. **The 9 runs may not be described as "9 consecutive identical runs."** They are two blocks
   of a changing extractor, and a pooled figure would describe no version that exists.
3. **Earlier figures from the retired engine are never pooled with these.** The extractor moved
   from Anthropic `claude-opus-5` to `gemini-3.8-flash` on Vertex at commit `038dbe6`. A
   statistic spanning both would describe no engine. This mistake was made once during the
   build and is written up in `docs/decisions/ADR-002-eval-loop.md`.
4. **Extraction recall is 85%, not 100%,** and the misses are visible. All 8 are `illustrative`
   or `structural` rows — teaching examples the judge would have skipped anyway — so the misses
   cost coverage, not correctness. That is luck about which rows were missed, and it is reported
   as luck.
5. **The extractor over-reads, and a prompt could not fix it.** It re-read numbers from a single
   sentence; the prompt already forbade it and the model did not always comply, so the guard is
   now code (`_drop_same_sentence_repeats`), where the project's own rule says decisions belong.
   The obvious rule — keep the longest quote — was **measured and rejected**: it dropped a
   labelled row and took recall from 46/54 to 45/54. Keeping the first mention holds recall at
   46/54 and removes 4 redundant verdicts.
6. **One defect class is still open, by design.** A labelled quote spanning a sentence boundary
   (*"Ten thousand dollars. It showed up, it is yours"*) is beyond the same-sentence guard's
   reach, and the extractor quoting one sentence there is correct.
7. **No false correction reached a creator in any scan on record.** In today's live
   back-catalogue scan (83 verdicts, all five rule families firing), duplicate `stale_*`
   corrections: **0**, and every verdict carried its `rule_fired`. In the last scan written up
   in ADR-002, all 85 quotes were verbatim in their cited source under the repo's own
   normaliser. Both are measurements over this corpus, not a guarantee about every corpus.
8. **The judge draws a hard line at "I don't know."** A past value the catalog contradicts
   returns `unresolved`, not a correction. Almanac has no verdict for "the creator
   misremembered", and inventing one would assert more than the rules can ground.
9. **Negative assertions carry a presence control.** The console-error test drives the same
   counter against a deliberately broken page first; the write-guard test drives the real
   `videos.update` counter to 1 by hand before asserting it stays 0. A zero that cannot register
   a one proves nothing.
10. **The corpus is synthetic.** Five caption files and two scripts written for the demo, on a
    test channel. The pipeline is real; the channel is not a real creator's.

### Built with

Python 3.12 · FastAPI · Uvicorn · Pydantic · **Gemini 3.8 Flash on Google Vertex AI** (project
`polygraph-hackathon`, `temperature=0.0`, `seed=20260908`) · YouTube Data API v3 over owner OAuth
· rates from Treasury, BLS, Freddie Mac and the New York Fed · GitHub Actions · pytest ·
Playwright.

---

## Before you submit — what changed, and what is left

**Re-verified this session against `main` @ `89085f7`, merged into this branch.** Three of the
four blockers recorded earlier are resolved. None of it was taken on report — every line below
was re-run here.

**1. The live URL exists.** Checked directly:
```
GET  https://almanac-gamma.vercel.app/            -> HTTP 200 · 26,600 bytes · 0.18s
GET  https://almanac-gamma.vercel.app/api/report  -> HTTP 200 · 5 videos · 83 verdicts
                                                     83/83 carry rule_fired
POST https://almanac-gamma.vercel.app/api/lint    -> HTTP 200 · 34.6s · 12 verdicts
                                                     12/12 carry rule_fired
                                                     stale_material   7000 -> 7500 (R5.stale_material)
                                                     stale_immaterial 6.85 -> 6.71
```
Paste the **alias**, not the per-deploy hostname.

**2. `reports/latest.json` is now committed** (`ff0e4ff`), so the 503-on-a-fresh-host problem is
gone. The suite is **290 passed** on the merged tree, with no artifact step needed first.

**3. `keepalive.yml` is real and `ALMANAC_URL` is set** — but **no run has ever fired**
(`gh run list` is empty). The cron is `*/10 12-22 8 9 *`, so it should begin at 12:00 UTC on
8 September. Nothing has proven it works end to end. Trigger it once by hand
(`gh workflow run keepalive`) rather than trusting it for the judging window untested.

**4. Devpost's students-only rule.** Confirm the profile shows `rk3469@columbia.edu`.

**Unchanged:** there is still no demo video. Leave the field blank rather than linking a
placeholder.

## The code-freeze tag

Run this **after** the Devpost form is submitted, on the exact commit you submitted.

```bash
git checkout main && git pull --ff-only
git tag -a v1.0-submitted -m "Devpost submission — AI Content Engine Hackathon"
git push origin v1.0-submitted
git rev-parse HEAD v1.0-submitted^{commit}   # the two SHAs must match
```

After the tag: `README.md` typo fixes only, and only before 07:00 CT.

## The 08:30 ET phone check

Set an alarm for **Tue 9/8, 08:30 ET**. Open the live URL and confirm it answers before judging
opens at 09:00 ET. If there is no live URL, the repo link is the submission and this check is
moot — say so rather than leaving the proof open.
