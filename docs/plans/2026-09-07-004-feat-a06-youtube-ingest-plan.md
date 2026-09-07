# A-06 (HAC-42) — Seed the real test channel and read it back over owner OAuth

- Date: 2026-09-07
- Branch: `claude/competent-galileo-832037`
- Scope: **REDUCED by Zaeem's decision.** Proofs 1, 2, 4 are in scope. Proof 3 is DEFERRED.

## Why proof 3 is deferred

The issue lists "A-04 PASS" as a precondition. That is only true of

    PROOF A-06: scan --source youtube status counts == scan --source corpus status counts

which needs the `scan` command A-04 builds. A-04 has not started — it is blocked on A-03, which
is executing right now in a parallel worktree. Deferring one proof is cheaper than blocking the
whole issue behind two unstarted ones. A-06 therefore does **not** close on this run.

Design consequence: `youtube.py` must return the *same shape* the corpus path yields
(`source_id`, `text`, `locators`) so A-04's `scan` can consume either source behind one
interface and `--source youtube` slots in with no rework.

## Constraints taken as given (not re-derived)

- ADR-000 §3 signatures are verified against discovery rev 20260820. Use them as written.
- `captions.download(...).execute()` returns **bytes**, not a dict (`supportsMediaDownload`).
- **`videos.update` is not called anywhere in this issue.** No write path, no `--apply`.
- ADR-000 gap **G-2 is open**: `tfmt` is typed `string` with no enum, so `"srt"` is unverified.
  This issue closes G-2 by asserting it live and appending the result to ADR-000.
- A parallel session owns `almanac/models.py` and `almanac/extract.py`. Do not touch them.
  Tests go in a **new** file `tests/test_youtube.py`. CLI wiring is the **last** commit.

## Environment findings that shaped the build

1. The worktree does not carry `venv`, `.env`, `token.json` or `client_secret.json` — they are
   gitignored and live only in the main checkout. Symlinked in; `venv` added to the worktree's
   local exclude so `git status` stays clean.
2. **ffmpeg 8.1.1 here is built without libfreetype and without libass** — there is no
   `drawtext` filter and no `subtitles` filter. Neither ImageMagick, `rsvg-convert`, PIL,
   nor cairosvg is installed. So the renderer cannot ask ffmpeg to draw text.
   Chosen path: emit each card as a hand-written one-page **PDF** using the base-14
   Helvetica fonts (no font embedding, no new dependency), rasterise with macOS `sips`, then
   let ffmpeg do only what it can do — sequence still images into H.264.
   Verified end to end before committing to it: 834-byte PDF → 1280×720 PNG with crisp text.

## Steps, each with its check

| # | Step | Check |
|---|---|---|
| 1 | `scripts/render_slides.py` — 4 cards/video (title + 3 numeric bullets pulled from the captions), 32 s each | `ffprobe` reports 20 s ≤ duration ≤ 60 s and 1280×720 for all five; eyeball one card |
| 2 | `scripts/seed_channel.md` — exact Studio click-path, PUBLIC, corpus title/description, `captions.srt` as the English track | Handed to Zaeem **before** anything else is built; his uploads run in parallel with steps 3–5 |
| 3 | `almanac/youtube.py` — `list_channel_videos()`, `download_captions()`, both owner-OAuth, read-only | `tests/test_youtube.py` green against recorded fixtures; no network in unit tests |
| 4 | `corpus/channel/*/meta.json` — real `video_id`s | surgical edit of that one field only; `git diff` shows one changed line per file |
| 5 | Live proofs 1, 2, 4 | printed and appended to `docs/proofs/A-06.md` |
| 6 | CLI wiring (`youtube` subcommand) — **last, own commit** | `almanac youtube --list` prints 5 rows |

## Proofs

In scope, each printed to the transcript and appended to `docs/proofs/A-06.md`:

    PROOF A-06: list_channel_videos returns 5 ids matching corpus meta.json = PASS
    PROOF A-06: download_captions(V1) parses with srt lib and word-diff vs corpus captions.srt < 2% = PASS
    PROOF A-06: quota used for the full scan < 1,500 units = PASS

Deferred, recorded as DEFERRED in the proof file, **not** counted as passing:

    PROOF A-06: scan --source youtube status counts == scan --source corpus status counts

## Guarding against the two lessons that cost real time

- **Assert the size of both sides.** A-01 shipped a url-200 test that silently skipped 2 of 29
  rows and still said PASS. Proof 1 therefore asserts `len(api_ids) == 5` **and**
  `len(corpus_ids) == 5` **and** set equality — a missing upload must not look like a match.
- **A negative assertion needs an adversarial probe.** The <2% word-diff in proof 2 is run
  first against a deliberately corrupted caption and must come out **red** before its green
  result on the real caption is believed. A threshold that cannot fail is not a threshold.

## Quota arithmetic (counted, not estimated)

Per YouTube Data API v3 published costs: a `list` on any resource is 1 unit;
`captions.download` is **200**.

    channels.list(mine=true)      1 ×   1 =   1
    playlistItems.list            1 ×   1 =   1
    videos.list                   1 ×   1 =   1
    captions.list                 5 ×   1 =   5
    captions.download             5 × 200 = 1000
                                  -----------
                                  total    1008   < 1500 ✓

Headroom to the budget is 492 units, which is 2 spare caption downloads. The daily project
quota is 10,000, so a full scan is ~10% of a day.

## Failure handoff

After 2 failed attempts at one step or 30 minutes with no passing proof: write
`docs/blockers/A-06.md`, print `BLOCKED A-06`, stop. The fallback the issue itself authorises
is that `scan --source corpus` is already the demo path, A-07 becomes a dry-run diff, and the
README says "YouTube ingest: implemented, blocked on OAuth app verification".
