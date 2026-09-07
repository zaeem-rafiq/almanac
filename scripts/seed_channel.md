# Making the five test videos public (A-06) — YOUR ~2 MINUTES

**The uploads are done.** All five videos are on the `almanac` channel with their titles,
descriptions and English caption tracks already attached, uploaded over the API by
`scripts/seed_uploads.py`. The read path has been proven against them: all three in-scope A-06
proofs PASS, with a 0.0000% caption round-trip difference on every video.

One thing is left, and only you can do it.

## Why this last step is manual

`videos.insert` from an app that has not passed Google's OAuth verification is **forced** to
`privacyStatus: private`, and a request asking for `public` is ignored. Confirmed live, not
assumed. A private video cannot be opened by a judge from the review page, which is the whole
point of seeding a real channel. So the visibility toggle is yours.

## What to do

Sign in at **studio.youtube.com** as the **`almanac` Brand Account** (check the top-right
avatar — picking the personal account is what cost A-00 two failed attempts), then:

**Content** → for each of the five rows below: the **Visibility** column → **Public** → **Save**.

Or open each link directly and use the visibility control on its edit page.

| # | Video | Watch link | Studio edit link |
|---|---|---|---|
| 1 | 401(k) limits explained (and the 3 mistakes  | [9A126b64qug](https://youtu.be/9A126b64qug) | [edit](https://studio.youtube.com/video/9A126b64qug/edit) |
| 2 | Pay off your mortgage or invest? The honest  | [Bzewy8DWGlo](https://youtu.be/Bzewy8DWGlo) | [edit](https://studio.youtube.com/video/Bzewy8DWGlo/edit) |
| 3 | HSA: the triple tax advantage almost nobody  | [di6RXMzVjQQ](https://youtu.be/di6RXMzVjQQ) | [edit](https://studio.youtube.com/video/di6RXMzVjQQ/edit) |
| 4 | How I'd invest $10,000 right now | [6o6TiXPHlu0](https://youtu.be/6o6TiXPHlu0) | [edit](https://studio.youtube.com/video/6o6TiXPHlu0/edit) |
| 5 | I-bonds vs high-yield savings: which one act | [3dil-DquuG0](https://youtu.be/3dil-DquuG0) | [edit](https://studio.youtube.com/video/3dil-DquuG0/edit) |

Bulk option: on the **Content** page, tick the header checkbox to select all five, then
**Edit → Visibility → Public → Update videos**. That is one operation instead of five.

## Then tell me

Nothing else is needed from you. I will re-run:

```bash
venv/bin/python scripts/check_youtube.py
```

to confirm the proofs still pass against public videos and to record the final state.

## What is already true, so you do not need to check it

- Titles and descriptions match `corpus/channel/*/meta.json` exactly — they were sent by API,
  not typed, so there is no transcription risk.
- English caption tracks are attached to all five and download back byte-identical
  (0.0000% word difference across every video).
- `video_id` is written into each `meta.json`.
- Each video is flagged `containsSyntheticMedia: true`, because the narration is text-to-speech.
- Audience is set to *not made for kids*.

## If something looks wrong

- **A video is stuck processing** — a ~2m40s 1280x720 file finishes in a minute or two. If one
  hangs past five, tell me and I will re-upload that single video.
- **You see more than five videos** — something was uploaded twice. Tell me before deleting
  anything; `scripts/seed_uploads.py` is idempotent and skips titles already on the channel, so
  a duplicate means something else happened.
- **Wrong account** — do not delete anything yourself. Any channel other than
  `ALMANAC_TEST_CHANNEL_ID` is a protected path for this project.

