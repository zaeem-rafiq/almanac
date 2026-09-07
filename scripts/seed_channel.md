# Seeding the Almanac test channel (A-06) — YOUR ~25 MINUTES

Zaeem: this is the one part of A-06 that cannot be automated. **Do this now**; everything
else in the issue is being built in parallel and does not block on you finishing.

## Why these five uploads are manual

The YouTube Data API forces `privacyStatus: private` on every `videos.insert` from an app
that has not passed Google's OAuth verification, and it ignores any attempt to set
`public`. Our app is in Testing mode. A private video cannot be opened by a judge from the
review page, and the point of seeding a real channel is that the demo links to a real,
clickable video. So the uploads go through YouTube Studio by hand, and the code only ever
**reads** the channel back.

Nothing in this issue calls `videos.update`. There is no write path to review.

## Before you start

- Sign in as the **`almanac` Brand Account** (channel `UCb0N54LdHk-10KgrL4n3LIA`), not your
  personal Google account. Picking the personal account is what cost A-00 two failed
  attempts. Check the avatar in the top-right of Studio before uploading anything.
- The five rendered MP4s are in **`renders/`** at the repo root. Each is 30 s, 1280x720,
  under 0.2 MB.
- Each video's caption file is the `captions.srt` already sitting in its corpus folder.

## The click-path, once per video

1. Go to **studio.youtube.com** → confirm the top-right avatar says **almanac**.
2. **Create** (top right) → **Upload videos**.
3. Drag in the MP4 named in the table below, or **Select files**.
4. **Title** — clear the auto-filled filename and paste the title from the table
   **exactly**. The title is how the code maps each uploaded video back to its corpus
   folder, so a stray character or a trailing space breaks the mapping.
5. **Description** — paste the description from the table.
6. *Audience* → **"No, it's not made for kids."**
7. Click **Show more** → **Language** → **English**. Captions cannot be attached until a
   video language is set; this is the step people skip.
8. Still under Show more → **Subtitles** → **Upload file** → **Without timing? No** →
   choose that video's `captions.srt` → **Done**. (If Studio's layout differs, the same
   upload is reachable after publishing from **Subtitles** in the left sidebar.)
9. **Next** (Video elements) → **Next** (Checks) → **Next** (Visibility).
10. Visibility → **Public** → **Publish**.
11. Copy the video URL it offers you and paste it into the checklist at the bottom of this
    file. You do not have to — the code finds the ids by matching titles — but it is a
    30-second insurance policy against a typo in a title.

Repeat for all five. Order does not matter.

## What to upload, verbatim

### 1. `renders/v1-401k-limits-explained.mp4`

**Title** (copy exactly):

```
401(k) limits explained (and the 3 mistakes I see every year)
```

**Description**:

```
The contribution limit, what it actually covers, and the three mistakes that cost people the most money.
```

**Captions file**: `corpus/channel/v1-401k-limits-explained/captions.srt`

### 2. `renders/v2-mortgage-or-invest.mp4`

**Title** (copy exactly):

```
Pay off your mortgage or invest? The honest answer
```

**Description**:

```
Guaranteed return versus expected return, and the three things the spreadsheet leaves out.
```

**Captions file**: `corpus/channel/v2-mortgage-or-invest/captions.srt`

### 3. `renders/v3-hsa-triple-tax-advantage.mp4`

**Title** (copy exactly):

```
HSA: the triple tax advantage almost nobody uses properly
```

**Description**:

```
The limits, the receipt trick that makes this account extraordinary, and the boundaries.
```

**Captions file**: `corpus/channel/v3-hsa-triple-tax-advantage/captions.srt`

### 4. `renders/v4-how-id-invest-10000.mp4`

**Title** (copy exactly):

```
How I'd invest $10,000 right now
```

**Description**:

```
Four steps, three funds, and every number in it is an illustration rather than a quote.
```

**Captions file**: `corpus/channel/v4-how-id-invest-10000/captions.srt`

### 5. `renders/v5-ibonds-vs-high-yield-savings.mp4`

**Title** (copy exactly):

```
I-bonds vs high-yield savings: which one actually wins?
```

**Description**:

```
The composite rate, the twelve month lockup, and why the rate everybody remembers is gone.
```

**Captions file**: `corpus/channel/v5-ibonds-vs-high-yield-savings/captions.srt`

## When you are done

Tell me, and I will run:

```bash
venv/bin/python -m almanac youtube --list
```

which reads the channel back over owner OAuth and writes the real `video_id` into each
`corpus/channel/*/meta.json`. That is the only field in `corpus/` this issue touches.

**Captions lag.** A track uploaded through Studio often takes a few minutes to show up in
`captions.list`, and occasionally longer while the video finishes processing. The proof
harness waits and retries rather than calling it a failure — if it reports missing captions
after its retry window, give it five more minutes and re-run before treating it as broken.

## Checklist

| # | Video | Uploaded public | Captions attached | Video id (optional) |
|---|---|---|---|---|
| 1 | `v1-401k-limits-explained` | ☐ | ☐ | |
| 2 | `v2-mortgage-or-invest` | ☐ | ☐ | |
| 3 | `v3-hsa-triple-tax-advantage` | ☐ | ☐ | |
| 4 | `v4-how-id-invest-10000` | ☐ | ☐ | |
| 5 | `v5-ibonds-vs-high-yield-savings` | ☐ | ☐ | |

## If something goes wrong

- **"Select files" is greyed out / no Create button** — you are on youtube.com, not
  studio.youtube.com, or the account has no channel. Re-check the avatar.
- **Subtitles section missing** — the video language is not set. Go back to step 7.
- **Upload stuck in processing** — a 30 s 1280x720 file processes in well under a minute.
  If it hangs past five, re-upload that one file; do not re-do the others.
- **You picked the wrong account** — delete the uploads from that channel, switch account,
  start over. Do not leave them up: any channel other than `ALMANAC_TEST_CHANNEL_ID` is a
  protected path for this project and must not be touched by Almanac.

