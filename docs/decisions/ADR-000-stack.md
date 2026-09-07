# ADR-000 — Stack, verified call signatures, and gaps

- Status: Accepted
- Date: 2026-09-07
- Issue: A-00 (HAC-36), step 2 "self-discover before scaffolding"
- Rule invoked: *"Verify every library call against the installed package docs before using it."* and
  *"Do not guess an API — an unverified call is listed as a gap, not used."*

## 1. Toolchain

| Component | Version | How verified |
|---|---|---|
| CPython | 3.12.12 | `uv venv --python 3.12` → `Using CPython 3.12.12` |
| venv | `venv/` at repo root | created by `uv venv` (uv 0.11.23) |
| git | 2.50.1 (Apple Git-155) | `git --version` |
| gh | 2.96.0, authed as `zaeem-rafiq` | `gh auth status` |

## 2. Installed package versions

Resolved by `uv pip install` into `venv/`, read back with `importlib.metadata.version`:

anthropic==1.4.0
google-api-python-client==2.200.0
google-auth-oauthlib==1.4.1
google-auth==2.57.1
requests==2.34.2
pydantic==2.13.5
pyyaml==6.0.3
srt==3.5.3
fastapi==0.141.1
uvicorn==0.52.4
pytest==9.1.1

## 3. YouTube Data API v3 — signatures verified offline

`google-api-python-client` ships the discovery document, so every signature below was read from
the installed package rather than from the web — no network, no API key, no guessing.

- Source read: `venv/lib/python3.12/site-packages/googleapiclient/discovery_cache/documents/youtube.v3.json`
- Discovery revision: **20260820**, API version **v3**

| Call | httpMethod / path | Required | Verified notes |
|---|---|---|---|
| `captions().list(part=, videoId=)` | GET `youtube/v3/captions` | `part`, `videoId` | `part` is repeated (list or comma string); returns `CaptionListResponse` |
| `captions().download(id=, tfmt=)` | GET `youtube/v3/captions/{id}` | `id` | `supportsMediaDownload: True` → `.execute()` returns **bytes**, not dict. `tfmt` optional |
| `videos().list(part=, id=)` | GET `youtube/v3/videos` | `part` | `id` repeated; `maxResults` integer |
| `videos().update(part=, body=)` | **PUT** `youtube/v3/videos` | `part` | request body schema `Video`, response `Video` |
| `channels().list(part=, mine=True)` | GET `youtube/v3/channels` | `part` | `mine` is a real boolean parameter — confirmed present |
| `playlistItems().list(part=, playlistId=)` | GET `youtube/v3/playlistItems` | `part` | `playlistId` / `videoId` / `pageToken` optional |

OAuth scope `https://www.googleapis.com/auth/youtube.force-ssl` is confirmed present in the
discovery document's `auth.oauth2.scopes` list.

### 3a. Consequence for the write guard (A-07)

`videos.update` is a **PUT**, and `VideoSnippet` carries
`categoryId, channelId, channelTitle, defaultAudioLanguage, defaultLanguage, description,
liveBroadcastContent, localized, publishedAt, tags, thumbnails, title`.
A PUT with a partial snippet drops the omitted fields. Therefore write-back must
**read the snippet with `videos().list(part="snippet")` first, mutate only `description`, and
send the whole snippet back** with the video `id`. This is a design constraint, not a preference.

## 4. Anthropic SDK — tool use with a Pydantic-derived schema

Verified by introspecting the installed SDK (`inspect.signature`), not from memory:

- `client.messages.create(...)` accepts: `model, max_tokens, messages, system, tools, tool_choice,
  stop_sequences, temperature*, stream, thinking, ...` (`tools: Iterable[ToolUnionParam]`,
  `tool_choice: ToolChoiceParam`).
- `anthropic.types.ToolParam.__annotations__` keys include **`name`, `description`, `input_schema`**
  (plus `cache_control, strict, type, input_examples, ...`). So a tool is
  `{"name": ..., "description": ..., "input_schema": <JSON Schema>}`.
- `anthropic.types.ToolUseBlock` exists → the structured result is read off content blocks of
  `type == "tool_use"`.
- Pydantic → JSON Schema is `Model.model_json_schema()`. Confirmed by generating one: a nested
  model emits **`$defs` + `$ref`**, e.g. `{"properties": {"claims": {"items": {"$ref":
  "#/$defs/Claim"}, "type": "array"}}, "$defs": {"Claim": {...}}, "required": ["claims"]}`.

This is the intended shape for `extract.py` and `notes.py` — the only two modules permitted to
call a model.

## 5. FMP — endpoint and field shapes confirmed against live data

Confirmed the endpoint vocabulary and response shape against FMP's live service. Every name in the
issue is real and every one returns ≥1 row:

| Call | Rows | Response shape |
|---|---|---|
| `economics-indicators?name=federalFunds` | ≥3 | `{"name","date","value"}`, monthly |
| `economics-indicators?name=30YearFixedRateMortgageAverage` | ≥13 | `{"name","date","value"}`, weekly |
| `economics-indicators?name=CPI` | ≥2 | `{"name","date","value"}`, monthly |
| `treasury-rates` | ≥64 | `{"date","month1"…"year10"…"year30"}`, **daily**, `year10` present |

`treasury-rates` takes no `name` parameter; the maturity is a **field**, so `year10` is read off the
newest row. `economics-indicators` requires `name`. Both accept `from_date` / `to_date`.

### 5a. Finding — `CPI` is an index level, not an inflation rate

`CPI` returns `326.031` (an index), not `~3.0` (a percent). A creator's claim
"inflation is 3%" therefore **cannot be compared to a single CPI row**. Almanac must compute
year-over-year from two CPI observations 12 months apart, and `facts/rates.json` should store the
derived YoY percent alongside the raw index, with `fetched_via` recording the derivation. This
changes the `rates` key set and belongs in the A-01 catalog design.

### 5b. Finding — three of the four series lag; treasury does not

Observed newest dates: `treasury-rates` **2026-09-04** (current), but `federalFunds`
**2025-12-01**, `CPI` **2025-12-01**, `30YearFixedRateMortgageAverage` **2025-12-04** — roughly
nine months stale relative to today. If that lag is also present on the REST endpoint used by
`catalog.refresh_rates()`, a naive `max_age_days` check marks Almanac's own rates table stale on
day one and the demo self-destructs. See Gap G-1.

## 6. Gaps — verified as unknown, therefore not used

| # | Gap | Resolution step |
|---|---|---|
| G-1 | Whether the **REST** `economics-indicators` endpoint with `FMP_API_KEY` returns fresher rows than the ~9-month lag observed. Also the exact base URL/version for the account's plan. | `scripts/preflight.py` prints the newest `date` per series; if the lag persists, `max_age_days` must be set per series from observed cadence rather than assumed. |
| G-2 | `tfmt="srt"` is typed `string` with **no enum** in the discovery document, so the accepted value set is not verifiable offline. | Assert it live in A-06 against the test channel; fall back to another `tfmt` only if it 400s. |
| G-3 | Whether Anthropic's `input_schema` accepts Pydantic's `$defs`/`$ref` form or needs flattening. | Preflight's live Anthropic call is a **tool-use** call with a `$defs`-bearing schema, so A-00 proves it before A-03 depends on it. |
| G-4 | OAuth not yet performed — no `client_secret.json` present at the time of writing. Auth date to be appended here on first successful consent. | A-00 execution. Refresh tokens for an app in "Testing" expire after **7 days**; deadline is Tue Sep 8, so one consent covers the event. |
| G-5 | No `python-dotenv` in the approved package list, so `.env` is parsed by a small hand-rolled reader in `almanac/` rather than a new dependency. | Recorded here as a deliberate choice; no dependency added without Zaeem's approval. |

## 7. OAuth authorisation log

| Date authorised | Channel id | Scope | Notes |
|---|---|---|---|
| _pending A-00 execution_ | — | `.../auth/youtube.force-ssl` | 7-day refresh-token expiry for Testing-mode apps |

---

## 8. Gap resolutions observed during A-00 execution (2026-09-07)

Appended per the append-only rule. Each answer below is an observation against live services
with Zaeem's own credentials, not a documentation claim.

### G-3 — RESOLVED. Anthropic accepts Pydantic's `$defs`/`$ref` schema.

`messages.create` with `tools=[{name, description, input_schema=Claims.model_json_schema()}]`
and `tool_choice={"type":"tool","name":"record_claims"}` against `claude-opus-5` returned a
`tool_use` block whose `input` validated cleanly through the nested model — 2 claims parsed.
**No schema flattening is needed.** `extract.py` (A-03/A-04) may use `model_json_schema()`
output directly.

### G-1 — RESOLVED, and it cost us a spec correction plus a confirmed hazard.

**(a) The endpoint path in the issue text is wrong.** `economics-indicators` returns HTTP 404
with an empty body. The live path is **`economic-indicators`** — singular "economic".
Verified by sweeping seven path spellings; only that one returned 200.

**(b) The legacy API generations are dead.** Both `/api/v3` and `/api/v4` now return HTTP 403:
*"Legacy Endpoint : Due to Legacy endpoints being no longer supported - This endpoint is only
available for legacy users who have valid subscriptions prior August 31, 2025."*
`/stable` is the only live generation, so `preflight.py`'s base-discovery loop was removed —
there is nothing to fall back to, and the loop was masking the real error by reporting only the
last base tried.

**(c) The staleness in §5b is REAL and reproduces on the REST path with our own key:**

| series | newest `date` | rows |
|---|---|---|
| `federalFunds` | 2025-12-01 | 3 |
| `30YearFixedRateMortgageAverage` | 2025-12-04 | 13 |
| `CPI` | 2025-12-01 | 2 |
| `treasury-rates` (`year10`) | **2026-09-04** | 63 |

Three of four series lag roughly nine months; only Treasury is current. **A-01 must set
`max_age_days` per series from these observed cadences.** A single global freshness threshold
makes Almanac declare its own rates table stale on day one.

Note also that `CPI` returns only 2 rows by default, but §5a's year-over-year derivation needs
observations 12 months apart — A-01 must pass `from_date`/`to_date` to widen the window.

### G-4 — NOT RESOLVED. OAuth consent succeeds; channel identity does not match.

Consent completed twice against client `476960580777-…apps.googleusercontent.com` with scope
`https://www.googleapis.com/auth/youtube.force-ssl`. Both times
`channels().list(part="id", mine=True)` returned **zero items**, while
`channels().list(part="id,snippet", id="UCvt3Yfxa3lq_FyO2gAr3aQQ")` returned one item
(title `Zaeem Khan`, published `2026-09-07T16:33:26Z`). The target channel is real; the
authorised Google identity does not own it. See `docs/blockers/A-00.md`.

The YouTube Data API v3 **is** enabled on project `almanac-hackathon` — an unenabled API
returns `accessNotConfigured`, and both calls above returned 200.

### G-2, G-5 — unchanged, open by design.

## 7a. OAuth authorisation log (continued)

| Date | Channel id returned by `mine=true` | Scope | Result |
|---|---|---|---|
| 2026-09-07 | *(none — 0 items)* | `.../youtube.force-ssl` | FAIL, wrong identity |
| 2026-09-07 | *(none — 0 items, forced `select_account consent`)* | `.../youtube.force-ssl` | FAIL, wrong identity |

### G-4 — RESOLVED 2026-09-07.

Root cause was identity, not configuration: the first two consents authorised a Google account
that owned no channel. Pointing `ALMANAC_TEST_CHANNEL_ID` at a genuine throwaway channel and
re-consenting as its owner produced a match on the first try.

| Date authorised | Channel id | Title | Scope | Refresh-token expiry (Testing mode) |
|---|---|---|---|---|
| **2026-09-07** | `UCb0N54LdHk-10KgrL4n3LIA` | `almanac` | `.../auth/youtube.force-ssl` | **2026-09-14** (7 days) |

Comfortably covers the Tue Sep 8 07:00 America/Chicago deadline. If work continues past
Sep 14, `rm token.json` and re-consent.

---

## 9. Correction to §5b / G-1c — the staleness is real, but the conclusion drawn from it was wrong

Appended 2026-09-07 during A-01 planning.

**What §5b got right:** FMP's `economic-indicators` really does return a ~9-month-old window on this
key. Confirmed again, and `from_date`/`to_date` are **ignored** — passing
`from_date=2026-06-01&to_date=2026-09-07` returns byte-identical rows to passing nothing.

**What §5b got wrong:** it concluded A-01 should therefore *loosen* `max_age_days` per series. That
is backwards. The right conclusion is that FMP's `economic-indicators` is **unusable** for Almanac,
and the primary feeds should be read directly — which is exactly what HAC-37's fallback clause
already authorises.

Two facts force this:

1. **`cpi_yoy` is not computable from FMP on this key.** The spec needs the latest month and the
   same month one year earlier. FMP returns **2 CPI rows, two months apart**. There is no pair.
2. **The issue's own `max_age_days` are tight** — mortgage 10 days, 10-year 5 days. Under a 9-month
   lag every rate is permanently stale, so the product would ship perpetually self-flagged.

**The four keyless primary feeds were tested and are current:**

| key | feed | observed |
|---|---|---|
| `fed_funds_effective` | NY Fed EFFR JSON | 3.63 @ 2026-09-03 |
| `mortgage_30y_fixed` | Freddie Mac `PMMS_history.csv` | 6.71 @ 2026-09-03 (2893 rows) |
| `treasury_10y` | Treasury daily par yield XML `BC_10YEAR` | 4.78 @ 2026-09-04 |
| `cpi_yoy` | BLS v1 `CUUR0000SA0` | 333.918 @ 2026-07, **33 months** of history |

Two of these match Zaeem's independently-recorded 9/7 figures exactly (3.63, 6.71), while FMP does
not — so the issue's "verified via FMP" note was in fact verified against the primary sources.

**Consequence for the runtime contract: none.** `facts/rates.json` keeps its shape; only
`fetched_via` changes from the hardcoded `"fmp"` to the actual fetcher per key (`nyfed`,
`freddiemac`, `treasury`, `bls`). `catalog.refresh_rates()` remains the only function that reaches
the network for rates, and every other module still reads `facts/rates.json` only.

**Open question for Zaeem (non-blocking):** if a different/paid FMP key returns current
`economic-indicators` rows, the original FMP path can be restored by swapping the key — the fetcher
seam is per-key, so it is a contained change either way.
