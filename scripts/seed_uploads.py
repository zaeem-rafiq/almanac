#!/usr/bin/env python3
"""A-06 — seed the test channel by API: upload private, attach captions, leave the rest to Zaeem.

WHY THIS EXISTS, AND WHY IT IS GUARDED LIKE A WRITE
---------------------------------------------------
`videos.insert` from an app that has not passed Google's OAuth verification is forced to
`privacyStatus: private`, and any attempt to set `public` is ignored. So this script cannot
finish the job: it uploads the five videos privately and attaches their caption tracks, and
Zaeem flips five visibility toggles in Studio. That trades ~25 minutes of manual uploading,
metadata entry and subtitle attachment for about two minutes of clicking.

The project rule says the write guard covers `videos.update` and then: *"Nothing else mutates
YouTube."* `videos.insert` is a mutation, so this script is an explicit, Zaeem-authorised
exception to that sentence — for seeding his own throwaway test channel, never a creator's.
Because it is an exception, it carries the SAME triple guard the rule demands of
`videos.update`, not a weaker one:

    1. ALMANAC_WRITE=true
    2. the authorised channel id equals ALMANAC_TEST_CHANNEL_ID
    3. the CLI was given --apply

Default is a dry run that prints exactly what would be sent. Nothing else in Almanac uploads.

Signatures verified offline against the discovery document bundled with
google-api-python-client, revision 20260820 (the same source ADR-000 §3 used):

    videos.insert(part=, body=, media_body=)    POST, required `part`, supportsMediaUpload
    captions.insert(part=, body=, media_body=)  POST, required `part`, supportsMediaUpload

SYNTHETIC MEDIA DISCLOSURE
--------------------------
The narration is text-to-speech, so `status.containsSyntheticMedia` is set to true. The field
is real (it is in the VideoStatus schema, checked, not assumed). Disclosing it is both what
YouTube asks for and the honest thing to do for a corpus whose entire purpose is to demonstrate
checking claims honestly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from almanac.cli import load_env  # noqa: E402
from almanac.youtube import (  # noqa: E402
    QuotaLedger,
    YouTubeError,
    authorised_channel,
    build_client,
    list_channel_videos,
    _normalise_title,
    write_video_ids,
)

CORPUS = ROOT / "corpus" / "channel"
RENDERS = ROOT / "renders"
# "People & Blogs" — a neutral category for a talking-head finance channel.
CATEGORY_ID = "22"
CAPTION_LANGUAGE = "en"
CAPTION_NAME = "English"


def planned_uploads() -> list[dict]:
    """One row per corpus video, with the file paths it needs. Fails loudly on a missing file."""
    rows = []
    for folder in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        video = RENDERS / f"{folder.name}.mp4"
        captions = folder / "captions.srt"
        if not video.is_file():
            raise FileNotFoundError(
                f"{video} is missing — run `venv/bin/python scripts/render_slides.py` first"
            )
        if not captions.is_file():
            raise FileNotFoundError(f"{captions} is missing")
        rows.append({
            "slug": folder.name,
            "title": meta["title"],
            "description": meta["description"],
            "video": video,
            "captions": captions,
            "size_mb": video.stat().st_size / 1e6,
        })
    return rows


def upload_video(youtube, row: dict, ledger: QuotaLedger) -> str:
    """videos.insert, resumable. Returns the new video id.

    privacyStatus is set to "private" explicitly rather than left to default: an unverified app
    has it forced anyway, and saying so in the request makes the intent legible to anyone
    reading the code later, instead of looking like an oversight that happened to be safe.
    """
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": row["title"],
            "description": row["description"],
            "categoryId": CATEGORY_ID,
            "defaultLanguage": CAPTION_LANGUAGE,
            "defaultAudioLanguage": CAPTION_LANGUAGE,
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    media = MediaFileUpload(str(row["video"]), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    ledger.record("videos.insert")

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"      … {int(status.progress() * 100):3d}%", end="\r", flush=True)
    return response["id"]


def attach_captions(youtube, video_id: str, srt_path: Path, ledger: QuotaLedger) -> str:
    """captions.insert with the corpus SRT as the English track."""
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "videoId": video_id,
            "language": CAPTION_LANGUAGE,
            "name": CAPTION_NAME,
            "isDraft": False,
        }
    }
    media = MediaFileUpload(str(srt_path), mimetype="application/octet-stream", resumable=False)
    ledger.record("captions.insert")
    return youtube.captions().insert(part="snippet", body=body, media_body=media).execute()["id"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the Almanac test channel (private uploads).")
    parser.add_argument("--apply", action="store_true", help="actually upload; default is a dry run")
    args = parser.parse_args(argv)

    load_env()
    rows = planned_uploads()

    print("Almanac A-06 — seed the test channel\n" + "-" * 76)
    for row in rows:
        print(f"  {row['slug']:<34} {row['size_mb']:>5.2f} MB  {row['title'][:44]}")
    print("-" * 76)

    ledger = QuotaLedger()
    youtube = build_client()
    channel = authorised_channel(youtube, ledger)  # raises unless it is the test channel
    print(f"  authorised channel: {channel['id']} ({channel['snippet']['title']})")

    existing = {_normalise_title(v["title"]): v["video_id"] for v in list_channel_videos(youtube, ledger)}
    todo = [r for r in rows if _normalise_title(r["title"]) not in existing]
    skipped = [r["slug"] for r in rows if _normalise_title(r["title"]) in existing]
    if skipped:
        print(f"  already on the channel, skipping: {', '.join(skipped)}")

    write_enabled = os.environ.get("ALMANAC_WRITE", "").lower() == "true"
    if not (write_enabled and args.apply):
        print("-" * 76)
        print(f"  DRY RUN — {len(todo)} video(s) would be uploaded PRIVATE with captions attached.")
        print(f"  ALMANAC_WRITE={'true' if write_enabled else 'false'}  --apply={args.apply}")
        print("  To go ahead:  ALMANAC_WRITE=true venv/bin/python scripts/seed_uploads.py --apply")
        return 0

    mapping = {r["slug"]: existing[_normalise_title(r["title"])] for r in rows if r["slug"] in skipped}
    for row in todo:
        print(f"  uploading {row['slug']} …")
        video_id = upload_video(youtube, row, ledger)
        print(f"      video_id={video_id}")
        caption_id = attach_captions(youtube, video_id, row["captions"], ledger)
        print(f"      caption track={caption_id[:16]}…")
        mapping[row["slug"]] = video_id

    changed = write_video_ids(mapping)
    print("-" * 76)
    print(f"  meta.json updated: {', '.join(changed) if changed else 'nothing to change'}")
    for line in ledger.breakdown():
        print(f"  {line}")
    print("-" * 76)
    print(f"  {len(todo)} uploaded PRIVATE. Now set each to Public in Studio, then run:")
    print("      venv/bin/python scripts/check_youtube.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
