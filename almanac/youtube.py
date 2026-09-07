"""YouTube Data API v3 access (owner OAuth). READ-ONLY in this issue.

RULE: `videos.update` runs only when ALL THREE guards hold:
  1. ALMANAC_WRITE=true
  2. the target channel id equals ALMANAC_TEST_CHANNEL_ID
  3. the CLI was given --apply
Default is dry-run (print the diff). Nothing else in this codebase mutates YouTube.

NOTE (ADR-000 3a): videos.update is a PUT and a partial snippet drops omitted fields.
Read the snippet with videos().list(part="snippet") first, mutate only `description`,
and send the whole snippet back.

**A-06 calls none of that.** This module is read-only: it lists the authorised channel's
uploads and downloads their caption tracks. There is no write path here to review.

Signatures are ADR-000 §3, read off the discovery document bundled with
google-api-python-client (revision 20260820). They are taken as verified and not re-derived.
The one thing ADR-000 could *not* settle offline is gap **G-2**: `tfmt` is typed `string`
with no enum, so `"srt"` is unverified. `probe_tfmt()` below settles it against the live
channel rather than assuming it.

Return shape
------------
`list_channel_videos()` and `iter_sources()` return `Source` records carrying
`source_id` / `text` / `locators` — deliberately the same shape the corpus reader yields,
so A-04's `scan` can consume `--source youtube` and `--source corpus` behind one interface
without either path knowing which it got. A-04 is free to lift `Source` and `Cue` out of
here into a shared module; they live here only because `almanac/models.py` belongs to a
session running in parallel with this one.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATH = REPO_ROOT / "token.json"
CLIENT_SECRET_PATH = REPO_ROOT / "client_secret.json"
CORPUS_DIR = REPO_ROOT / "corpus" / "channel"

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# Quota unit costs, from YouTube's published Data API v3 quota table. These are NOT in the
# discovery document — it carries no cost metadata — so they are documented values, while the
# CALL COUNTS the ledger records are observed. The proof reports both halves separately so
# nobody mistakes one for the other.
COST_LIST = 1
COST_CAPTION_DOWNLOAD = 200

# tfmt candidates for gap G-2, best first. "srt" is what the issue wants; the rest exist so a
# 400 produces a recorded working value instead of a guess.
TFMT_CANDIDATES = ("srt", "vtt", "ttml", "sbv")

# Studio-uploaded caption tracks routinely take a few minutes to appear in captions.list while
# the video is still processing. Absence inside that window is not evidence of failure.
CAPTION_WAIT_SECONDS = 180
CAPTION_POLL_SECONDS = 15


class YouTubeError(RuntimeError):
    """Raised for an Almanac-level failure (identity, missing track), not a transport error."""


@dataclass(frozen=True)
class Cue:
    """One caption cue. `index` is 1-based, matching SRT numbering."""

    index: int
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class Source:
    """One reviewable unit of a creator's back catalogue.

    Identical in shape whether it came from `corpus/channel/` or from the live channel, so a
    caller can hold both without branching. `locators` is what lets a verdict point back at
    "this sentence, at 04:12" rather than at the video as a whole.
    """

    source_id: str
    title: str
    published_at: str
    description: str
    text: str
    locators: tuple[Cue, ...]
    origin: str


@dataclass
class QuotaLedger:
    """Counts API calls and prices them, so the quota proof is arithmetic, not an estimate."""

    calls: dict[str, int] = field(default_factory=dict)

    def record(self, endpoint: str, count: int = 1) -> None:
        self.calls[endpoint] = self.calls.get(endpoint, 0) + count

    @property
    def units(self) -> int:
        total = 0
        for endpoint, count in self.calls.items():
            unit = COST_CAPTION_DOWNLOAD if endpoint == "captions.download" else COST_LIST
            total += unit * count
        return total

    def breakdown(self) -> list[str]:
        rows = []
        for endpoint in sorted(self.calls):
            count = self.calls[endpoint]
            unit = COST_CAPTION_DOWNLOAD if endpoint == "captions.download" else COST_LIST
            rows.append(f"{endpoint:<22} {count:>3} x {unit:>4} = {count * unit:>5}")
        rows.append(f"{'TOTAL':<22} {'':>3}   {'':>4}   {self.units:>5}")
        return rows


# ------------------------------------------------------------------------------ credentials

def load_credentials(token_path: Path | None = None):
    """Return owner OAuth credentials, refreshing but never re-consenting.

    Consent is deliberately NOT run from library code. A blocking browser flow inside what a
    caller thinks is a read is a nasty surprise, and A-00 already owns the consent path in
    `scripts/preflight.py`. If the token is unusable this raises and names that script.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path or TOKEN_PATH
    if not path.is_file():
        raise YouTubeError(
            f"missing {path.name}. Run `venv/bin/python scripts/preflight.py` to consent; "
            "at the account chooser pick the 'almanac' Brand Account, not the personal account."
        )
    creds = Credentials.from_authorized_user_file(str(path), YOUTUBE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json(), encoding="utf-8")
    if not creds.valid:
        raise YouTubeError(
            f"{path.name} is present but not valid. Delete it and re-run "
            "`venv/bin/python scripts/preflight.py` to re-consent."
        )
    return creds


def build_client(credentials=None):
    from googleapiclient.discovery import build

    return build(
        "youtube", "v3", credentials=credentials or load_credentials(), cache_discovery=False
    )


def authorised_channel(youtube, ledger: QuotaLedger) -> dict:
    """Return the authorised channel, refusing to proceed against any other channel.

    The project rule names every channel other than ALMANAC_TEST_CHANNEL_ID as a protected
    path. Enforcing that here means the guard sits on the read path too, not only on the
    write path A-07 will add — a read against the wrong channel is still the wrong channel.
    """
    ledger.record("channels.list")
    items = youtube.channels().list(part="id,snippet,contentDetails", mine=True).execute().get(
        "items", []
    )
    if not items:
        raise YouTubeError(
            "channels.list(mine=True) returned no items — the authorised Google account owns "
            "no channel. Re-consent and pick the 'almanac' Brand Account at the chooser."
        )
    channel = items[0]
    expected = os.environ.get("ALMANAC_TEST_CHANNEL_ID")
    if expected and channel["id"] != expected:
        raise YouTubeError(
            f"authorised channel {channel['id']} is not ALMANAC_TEST_CHANNEL_ID {expected}. "
            "Refusing to read any other channel."
        )
    return channel


# --------------------------------------------------------------------------------- listing

def list_channel_videos(youtube=None, ledger: QuotaLedger | None = None) -> list[dict]:
    """channels.list(mine=true) -> uploads playlist -> playlistItems.list -> videos.list.

    Returns one dict per upload with `video_id`, `title`, `description`, `published_at`,
    `duration`, `caption` (YouTube's own "does this video have captions" flag).

    Both list calls paginate. Five videos will never need a second page, but a back catalogue
    is exactly the thing that outgrows 50 items, and a silently truncated catalogue is the
    failure mode this project can least afford — Almanac's whole job is "every number in the
    back catalogue", so missing videos are missing verdicts.
    """
    youtube = youtube or build_client()
    ledger = ledger if ledger is not None else QuotaLedger()

    channel = authorised_channel(youtube, ledger)
    uploads_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    video_ids: list[str] = []
    page_token = None
    while True:
        ledger.record("playlistItems.list")
        response = (
            youtube.playlistItems()
            .list(part="contentDetails", playlistId=uploads_id, maxResults=50, pageToken=page_token)
            .execute()
        )
        video_ids.extend(item["contentDetails"]["videoId"] for item in response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    videos: list[dict] = []
    for start in range(0, len(video_ids), 50):
        chunk = video_ids[start:start + 50]
        ledger.record("videos.list")
        response = (
            youtube.videos()
            .list(part="snippet,contentDetails", id=",".join(chunk))
            .execute()
        )
        for item in response.get("items", []):
            snippet = item["snippet"]
            videos.append({
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "published_at": snippet.get("publishedAt", ""),
                "duration": item.get("contentDetails", {}).get("duration", ""),
                "caption": item.get("contentDetails", {}).get("caption", "false"),
            })
    return videos


# --------------------------------------------------------------------------------- captions

def _english_track(items: list[dict]) -> dict | None:
    """Prefer an uploaded English track over YouTube's machine transcription.

    trackKind 'ASR' is speech recognition. Almanac reads numbers off these captions and
    decides whether a creator's figure is stale; a misheard "twenty-three thousand" would
    become a fabricated claim attributed to the creator. Uploaded tracks only.
    """
    english = [i for i in items if i.get("snippet", {}).get("language", "").lower().startswith("en")]
    standard = [i for i in english if i.get("snippet", {}).get("trackKind") != "ASR"]
    if standard:
        return standard[0]
    non_asr = [i for i in items if i.get("snippet", {}).get("trackKind") != "ASR"]
    return non_asr[0] if non_asr else None


def list_caption_tracks(video_id: str, youtube=None, ledger: QuotaLedger | None = None) -> list[dict]:
    youtube = youtube or build_client()
    ledger = ledger if ledger is not None else QuotaLedger()
    ledger.record("captions.list")
    return youtube.captions().list(part="snippet", videoId=video_id).execute().get("items", [])


def wait_for_caption_track(
    video_id: str,
    youtube=None,
    ledger: QuotaLedger | None = None,
    timeout_s: float = CAPTION_WAIT_SECONDS,
    poll_s: float = CAPTION_POLL_SECONDS,
    sleep=time.sleep,
) -> dict:
    """Poll captions.list until an uploaded English track appears, or time out.

    Studio attaches a caption track asynchronously; polling is the difference between
    "not ready yet" and "broken", and only one of those is a failure.
    """
    youtube = youtube or build_client()
    ledger = ledger if ledger is not None else QuotaLedger()
    deadline = time.monotonic() + timeout_s
    while True:
        track = _english_track(list_caption_tracks(video_id, youtube, ledger))
        if track:
            return track
        if time.monotonic() >= deadline:
            raise YouTubeError(
                f"no uploaded English caption track on {video_id} after {timeout_s:.0f}s. "
                "Studio tracks can lag; confirm the track is attached and re-run."
            )
        sleep(poll_s)


def probe_tfmt(
    caption_id: str, youtube=None, ledger: QuotaLedger | None = None
) -> tuple[str, bytes]:
    """Settle ADR-000 gap G-2 by trying tfmt values against the live API.

    `tfmt` is typed `string` with no enum in the discovery document, so the accepted value set
    is not knowable offline. This tries "srt" first and returns whichever value the API
    actually accepted, so the ADR records an observation rather than an assumption.

    Costs 200 units per attempt — a rejected value still bills — which is why the ordering
    puts the wanted value first and why callers use `download_captions` once G-2 is settled.
    """
    youtube = youtube or build_client()
    ledger = ledger if ledger is not None else QuotaLedger()
    errors = []
    for tfmt in TFMT_CANDIDATES:
        ledger.record("captions.download")
        try:
            payload = youtube.captions().download(id=caption_id, tfmt=tfmt).execute()
        except Exception as exc:  # googleapiclient raises HttpError; any failure is a rejection
            errors.append(f"{tfmt}: {type(exc).__name__} {str(exc)[:120]}")
            continue
        if payload:
            return tfmt, payload
        errors.append(f"{tfmt}: accepted but returned an empty body")
    raise YouTubeError("no tfmt value was accepted — " + "; ".join(errors))


def download_captions(
    video_id: str,
    youtube=None,
    ledger: QuotaLedger | None = None,
    tfmt: str = "srt",
    wait: bool = True,
) -> str:
    """captions.list(part, videoId) then captions.download(id, tfmt="srt") -> SRT text.

    ADR-000 §3: `captions.download` carries `supportsMediaDownload: True`, so `.execute()`
    returns **bytes**, not a dict. Decoding that is this function's job, not the caller's.
    """
    youtube = youtube or build_client()
    ledger = ledger if ledger is not None else QuotaLedger()

    if wait:
        track = wait_for_caption_track(video_id, youtube, ledger)
    else:
        track = _english_track(list_caption_tracks(video_id, youtube, ledger))
        if track is None:
            raise YouTubeError(f"no uploaded English caption track on {video_id}")

    ledger.record("captions.download")
    payload = youtube.captions().download(id=track["id"], tfmt=tfmt).execute()
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    if isinstance(payload, str):
        return payload
    raise YouTubeError(
        f"captions.download returned {type(payload).__name__}, expected bytes "
        "(ADR-000 §3 records supportsMediaDownload: True)"
    )


# ----------------------------------------------------- the one shape both sources return

def cues_from_srt(srt_text: str) -> tuple[Cue, ...]:
    """Parse SRT into locators using the `srt` library, not a hand-rolled regex.

    The library is already a project dependency and it is the same parser A-02 used on the
    corpus, so corpus-side and API-side text cannot diverge through parser differences.
    """
    import srt as srt_lib

    return tuple(
        Cue(
            index=sub.index if sub.index is not None else i,
            start_s=sub.start.total_seconds(),
            end_s=sub.end.total_seconds(),
            text=sub.content.replace("\n", " ").strip(),
        )
        for i, sub in enumerate(srt_lib.parse(srt_text), start=1)
    )


def _flatten(cues: tuple[Cue, ...]) -> str:
    return " ".join(cue.text for cue in cues if cue.text)


def read_corpus_sources(corpus_dir: Path | None = None) -> list[Source]:
    """The `--source corpus` half of the interface. No network, no credentials."""
    import json

    directory = corpus_dir or CORPUS_DIR
    sources = []
    for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        cues = cues_from_srt((folder / "captions.srt").read_text(encoding="utf-8"))
        sources.append(
            Source(
                source_id=meta["video_id"],
                title=meta["title"],
                published_at=meta["published_at"],
                description=meta["description"],
                text=_flatten(cues),
                locators=cues,
                origin="corpus",
            )
        )
    return sources


def read_youtube_sources(youtube=None, ledger: QuotaLedger | None = None) -> list[Source]:
    """The `--source youtube` half of the interface — same `Source` records, live channel."""
    youtube = youtube or build_client()
    ledger = ledger if ledger is not None else QuotaLedger()

    sources = []
    for video in list_channel_videos(youtube, ledger):
        cues = cues_from_srt(download_captions(video["video_id"], youtube, ledger))
        sources.append(
            Source(
                source_id=video["video_id"],
                title=video["title"],
                published_at=video["published_at"],
                description=video["description"],
                text=_flatten(cues),
                locators=cues,
                origin="youtube",
            )
        )
    return sources


def iter_sources(origin: str = "corpus", **kwargs) -> list[Source]:
    """Single entry point A-04's `scan --source {corpus,youtube}` can call."""
    if origin == "corpus":
        return read_corpus_sources(**kwargs)
    if origin == "youtube":
        return read_youtube_sources(**kwargs)
    raise ValueError(f"unknown source {origin!r}; expected 'corpus' or 'youtube'")


# ------------------------------------------------------------- mapping uploads to the corpus

def _normalise_title(title: str) -> str:
    """Fold a title for comparison: case, whitespace and typographic punctuation only."""
    folded = title.strip().lower()
    for bad, good in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                      ("–", "-"), ("—", "-")):
        folded = folded.replace(bad, good)
    return " ".join(folded.split())


def match_videos_to_corpus(videos: list[dict], corpus_dir: Path | None = None) -> dict[str, str]:
    """Map corpus slug -> uploaded video id by title.

    Title is the join key because it is the one field Zaeem copies verbatim from
    `seed_channel.md`, and it is human-checkable if it goes wrong.

    Raises on an ambiguous title rather than silently taking the first match: two uploads with
    the same title would otherwise pin two corpus folders to one video and the mismatch would
    surface much later as a wrong verdict.
    """
    import json

    directory = corpus_dir or CORPUS_DIR
    by_title: dict[str, list[str]] = {}
    for video in videos:
        by_title.setdefault(_normalise_title(video["title"]), []).append(video["video_id"])

    mapping: dict[str, str] = {}
    for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        candidates = by_title.get(_normalise_title(meta["title"]), [])
        if len(candidates) > 1:
            raise YouTubeError(
                f"title {meta['title']!r} matches {len(candidates)} uploads {candidates} — "
                "delete the duplicate on the channel before mapping."
            )
        if candidates:
            mapping[folder.name] = candidates[0]
    return mapping


def write_video_ids(mapping: dict[str, str], corpus_dir: Path | None = None) -> list[str]:
    """Write the real `video_id` into each meta.json, touching that one field and nothing else.

    `corpus/**` is a protected path except for this field, so the edit is a targeted
    replacement of a single value in the parsed object, re-serialised with the same 2-space
    indentation the corpus already uses. Files whose id is already correct are left alone so
    the diff shows only what actually changed.
    """
    import json

    directory = corpus_dir or CORPUS_DIR
    changed = []
    for slug, video_id in sorted(mapping.items()):
        path = directory / slug / "meta.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        if meta.get("video_id") == video_id:
            continue
        meta["video_id"] = video_id
        path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        changed.append(slug)
    return changed
