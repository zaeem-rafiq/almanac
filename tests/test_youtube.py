"""A-06 tests for YouTube ingest.

Every test here runs OFFLINE against a fake client. The live channel proves the integration
once, in `scripts/check_youtube.py`; a unit suite that needs the network is a suite that stops
running the moment a refresh token expires, and this project's token is in Testing mode with a
7-day life.

Each proof gets a positive test AND a test that makes it fail. A-01 shipped two checks that
could not be made to fail and therefore proved nothing — the negative half is what turns the
positive half into evidence.
"""

from __future__ import annotations

import json
import shutil

import pytest

from almanac import youtube as yt
from scripts import check_youtube as cy


# ----------------------------------------------------------------------------- fake client

class _Request:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Resource:
    def __init__(self, handler):
        self._handler = handler

    def list(self, **kwargs):
        return _Request(self._handler("list", kwargs))

    def download(self, **kwargs):
        return _Request(self._handler("download", kwargs))


class FakeYouTube:
    """Mimics googleapiclient's `yt.captions().list(...).execute()` chaining."""

    def __init__(self, *, channels=None, playlist_items=None, videos=None,
                 captions=None, caption_bodies=None, accepted_tfmt="srt"):
        self.channels_payload = channels
        self.playlist_items_payload = playlist_items or []
        self.videos_payload = videos or []
        self.captions_payload = captions or []
        self.caption_bodies = caption_bodies or {}
        self.accepted_tfmt = accepted_tfmt
        self.seen: list[tuple[str, dict]] = []

    def channels(self):
        return _Resource(lambda verb, kw: self._record("channels.list", kw, self.channels_payload))

    def playlistItems(self):
        def handler(verb, kw):
            return self._record("playlistItems.list", kw, self.playlist_items_payload)
        return _Resource(handler)

    def videos(self):
        return _Resource(lambda verb, kw: self._record("videos.list", kw, self.videos_payload))

    def captions(self):
        def handler(verb, kw):
            if verb == "download":
                self.seen.append(("captions.download", kw))
                if kw.get("tfmt") != self.accepted_tfmt:
                    return RuntimeError(f"HTTP 400: tfmt {kw.get('tfmt')!r} not supported")
                return self.caption_bodies.get(kw["id"], b"")
            return self._record("captions.list", kw, {"items": self.captions_payload})
        return _Resource(handler)

    def _record(self, name, kwargs, payload):
        self.seen.append((name, kwargs))
        return payload


CHANNEL_OK = {
    "items": [{
        "id": "UCb0N54LdHk-10KgrL4n3LIA",
        "snippet": {"title": "almanac"},
        "contentDetails": {"relatedPlaylists": {"uploads": "UUb0N54LdHk-10KgrL4n3LIA"}},
    }]
}

SRT_ONE = (
    "1\n00:00:00,000 --> 00:00:02,800\nThe limit is 23000 dollars this year\n\n"
    "2\n00:00:03,000 --> 00:00:05,800\nand the catch up adds 7500 on top\n"
)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A writable copy of the corpus wired into both modules' path constants."""
    channel = tmp_path / "channel"
    shutil.copytree(cy.CHANNEL_DIR, channel)
    monkeypatch.setattr(cy, "CHANNEL_DIR", channel)
    monkeypatch.setattr(yt, "CORPUS_DIR", channel)
    return channel


def corpus_titles(channel_dir):
    return [
        json.loads((d / "meta.json").read_text())["title"]
        for d in sorted(p for p in channel_dir.iterdir() if p.is_dir())
    ]


def fake_uploads(channel_dir, ids=None):
    titles = corpus_titles(channel_dir)
    # A real YouTube id is exactly 11 URL-safe base64 chars; the fakes must be too, or the
    # placeholder check in prove_ids_match rejects them for the wrong reason.
    ids = ids or [f"vidFake{i:04d}" for i in range(len(titles))]
    return [{"video_id": i, "title": t, "description": "", "published_at": "",
             "duration": "PT30S", "caption": "true"} for i, t in zip(ids, titles)]


# --------------------------------------------------------------------------- quota ledger

def test_ledger_prices_the_documented_full_scan_at_1253_units():
    ledger = yt.QuotaLedger()
    ledger.record("channels.list")
    ledger.record("playlistItems.list")
    ledger.record("videos.list")
    ledger.record("captions.list", 5)
    ledger.record("captions.download", 5)
    assert ledger.units == 1 + 1 + 1 + 5 * 50 + 5 * 200 == 1253
    assert cy.prove_quota(ledger)[0]


def test_captions_list_is_not_priced_like_a_cheap_list():
    """The captions resource is expensive; an earlier ledger charged 1 and understated a scan.

    Pinned explicitly because the bug was invisible — everything still "passed", just against
    a number that was 245 units too low.
    """
    assert yt.QuotaLedger.cost("captions.list") == 50
    assert yt.QuotaLedger.cost("videos.list") == 1
    assert yt.QuotaLedger.cost("captions.download") == 200
    assert yt.QuotaLedger.cost("captions.insert") == 400


def test_an_unpriced_endpoint_is_never_silently_free():
    assert yt.QuotaLedger.cost("some.future.method") == yt.DEFAULT_COST >= 1


def test_quota_proof_fails_once_the_budget_is_exceeded():
    ledger = yt.QuotaLedger()
    ledger.record("captions.download", 8)  # 1600 units
    ok, detail = cy.prove_quota(ledger)
    assert not ok, detail
    assert "1600" in detail


def test_list_channel_videos_counts_every_call_it_makes(sandbox):
    videos = fake_uploads(sandbox)
    fake = FakeYouTube(
        channels=CHANNEL_OK,
        playlist_items={"items": [{"contentDetails": {"videoId": v["video_id"]}} for v in videos]},
        videos={"items": [
            {"id": v["video_id"], "snippet": {"title": v["title"], "description": "",
                                              "publishedAt": ""},
             "contentDetails": {"duration": "PT30S", "caption": "true"}}
            for v in videos
        ]},
    )
    ledger = yt.QuotaLedger()
    got = yt.list_channel_videos(fake, ledger)
    assert len(got) == 5
    assert ledger.calls == {"channels.list": 1, "playlistItems.list": 1, "videos.list": 1}
    assert ledger.units == 3


# ------------------------------------------------------------------------- channel guard

def test_reading_a_channel_other_than_the_test_channel_is_refused(monkeypatch):
    monkeypatch.setenv("ALMANAC_TEST_CHANNEL_ID", "UCsomeoneElsesChannel")
    fake = FakeYouTube(channels=CHANNEL_OK)
    with pytest.raises(yt.YouTubeError, match="Refusing to read any other channel"):
        yt.authorised_channel(fake, yt.QuotaLedger())


def test_a_google_account_owning_no_channel_is_named_as_such():
    fake = FakeYouTube(channels={"items": []})
    with pytest.raises(yt.YouTubeError, match="owns no channel"):
        yt.authorised_channel(fake, yt.QuotaLedger())


# ------------------------------------------------------------------- proof 1: ids match

def test_ids_match_passes_when_all_five_line_up(sandbox):
    videos = fake_uploads(sandbox)
    yt.write_video_ids(yt.match_videos_to_corpus(videos, sandbox), sandbox)
    ok, detail = cy.prove_ids_match(videos)
    assert ok, detail
    assert "matched=5" in detail


def test_ids_match_fails_when_one_upload_is_missing(sandbox):
    """The A-01 lesson: four of five must not look identical to five of five."""
    videos = fake_uploads(sandbox)
    yt.write_video_ids(yt.match_videos_to_corpus(videos, sandbox), sandbox)
    ok, detail = cy.prove_ids_match(videos[:-1])
    assert not ok
    assert "channel has 4 uploads" in detail


def test_ids_match_fails_on_an_upload_no_corpus_folder_claims(sandbox):
    videos = fake_uploads(sandbox)
    yt.write_video_ids(yt.match_videos_to_corpus(videos, sandbox), sandbox)
    extra = dict(videos[0], video_id="vidOrphan01", title="Some other video entirely")
    ok, detail = cy.prove_ids_match(videos + [extra])
    assert not ok
    assert "vidOrphan01" in detail


def test_ids_match_fails_while_meta_json_still_holds_a_placeholder(sandbox):
    """A placeholder id must never satisfy the proof.

    The corpus now carries real ids, so the placeholder is planted explicitly rather than
    relying on the corpus still being unseeded — otherwise this test would quietly stop
    testing anything the moment the channel was seeded.
    """
    videos = fake_uploads(sandbox)
    yt.write_video_ids(yt.match_videos_to_corpus(videos, sandbox), sandbox)

    slug = sorted(p.name for p in sandbox.iterdir() if p.is_dir())[0]
    path = sandbox / slug / "meta.json"
    meta = json.loads(path.read_text())
    meta["video_id"] = "placeholder-v1-401k-limits"
    path.write_text(json.dumps(meta, indent=2) + "\n")

    ok, detail = cy.prove_ids_match(videos)
    assert not ok
    assert "is not a real id" in detail


def test_duplicate_titles_on_the_channel_raise_rather_than_pick_one(sandbox):
    videos = fake_uploads(sandbox)
    videos.append(dict(videos[0], video_id="vidDupe0001"))
    with pytest.raises(yt.YouTubeError, match="matches 2 uploads"):
        yt.match_videos_to_corpus(videos, sandbox)


def test_write_video_ids_touches_only_the_video_id_field(sandbox):
    """`corpus/**` is protected except this one field."""
    path = sandbox / sorted(p.name for p in sandbox.iterdir() if p.is_dir())[0] / "meta.json"
    before = json.loads(path.read_text())
    yt.write_video_ids(yt.match_videos_to_corpus(fake_uploads(sandbox), sandbox), sandbox)
    after = json.loads(path.read_text())
    assert before["video_id"] != after["video_id"]
    assert {k: v for k, v in before.items() if k != "video_id"} == \
           {k: v for k, v in after.items() if k != "video_id"}


def test_write_video_ids_is_idempotent(sandbox):
    mapping = yt.match_videos_to_corpus(fake_uploads(sandbox), sandbox)
    assert yt.write_video_ids(mapping, sandbox)
    assert yt.write_video_ids(mapping, sandbox) == []


# ------------------------------------------------- proof 2: caption fidelity + its control

def test_identical_captions_score_zero_word_diff():
    assert cy.word_diff_ratio(SRT_ONE, SRT_ONE) == 0.0


def test_the_adversarial_control_actually_goes_red(sandbox):
    """The check that the check can fail. Without this, '< 2%' is decoration."""
    slug = sorted(p.name for p in sandbox.iterdir() if p.is_dir())[0]
    original = (sandbox / slug / "captions.srt").read_text()
    ratio = cy.word_diff_ratio(original, cy.corrupt_captions(original))
    assert ratio > cy.WORD_DIFF_THRESHOLD, (
        f"corrupted caption scored {ratio:.4%}, which the 2% threshold would have let through"
    )


def test_caption_fidelity_passes_on_a_faithful_round_trip(sandbox):
    slug = sorted(p.name for p in sandbox.iterdir() if p.is_dir())[0]
    original = (sandbox / slug / "captions.srt").read_text()
    ok, detail = cy.prove_caption_fidelity(slug, original)
    assert ok, detail
    assert "RED as required" in detail


def test_caption_fidelity_fails_on_a_corrupted_download(sandbox):
    slug = sorted(p.name for p in sandbox.iterdir() if p.is_dir())[0]
    original = (sandbox / slug / "captions.srt").read_text()
    ok, detail = cy.prove_caption_fidelity(slug, cy.corrupt_captions(original))
    assert not ok, detail


def test_caption_fidelity_fails_when_the_download_is_not_srt(sandbox):
    slug = sorted(p.name for p in sandbox.iterdir() if p.is_dir())[0]
    ok, detail = cy.prove_caption_fidelity(slug, "WEBVTT\n\nnot an srt file at all")
    assert not ok
    assert "parse" in detail or "zero cues" in detail


def test_cosmetic_recue_stays_under_the_threshold(sandbox):
    """YouTube re-segments cues on round-trip. That is not content drift and must not fail."""
    import srt as srt_lib

    slug = sorted(p.name for p in sandbox.iterdir() if p.is_dir())[0]
    original = (sandbox / slug / "captions.srt").read_text()
    subs = list(srt_lib.parse(original))
    for sub in subs:
        sub.content = sub.content.upper()  # casing only
    ok, detail = cy.prove_caption_fidelity(slug, srt_lib.compose(subs))
    assert ok, detail


# ------------------------------------------------------------------------ caption download

def test_download_captions_decodes_the_bytes_adr_000_documented():
    """ADR-000 §3: supportsMediaDownload: True means execute() returns bytes, not a dict."""
    fake = FakeYouTube(
        captions=[{"id": "cap1", "snippet": {"language": "en", "trackKind": "standard"}}],
        caption_bodies={"cap1": SRT_ONE.encode("utf-8")},
    )
    text = yt.download_captions("vid1", fake, yt.QuotaLedger(), wait=False)
    assert text == SRT_ONE
    assert len(yt.source_from_srt("vid1", text).locators) == 2


def test_machine_transcription_is_never_preferred_over_an_uploaded_track():
    """An ASR mishearing would become a fabricated claim attributed to the creator."""
    track = yt._english_track([
        {"id": "asr", "snippet": {"language": "en", "trackKind": "ASR"}},
        {"id": "real", "snippet": {"language": "en", "trackKind": "standard"}},
    ])
    assert track["id"] == "real"


def test_a_missing_caption_track_is_an_error_not_an_empty_string():
    fake = FakeYouTube(captions=[])
    with pytest.raises(yt.YouTubeError, match="no uploaded English caption track"):
        yt.download_captions("vid1", fake, yt.QuotaLedger(), wait=False)


def test_wait_for_caption_track_retries_before_giving_up():
    """Studio tracks lag. Absence inside the window is 'not ready', not 'broken'."""
    fake = FakeYouTube(captions=[])
    calls = {"n": 0}

    def appear_on_third_poll(_seconds):
        calls["n"] += 1
        if calls["n"] >= 2:
            fake.captions_payload = [
                {"id": "cap1", "snippet": {"language": "en", "trackKind": "standard"}}
            ]

    track = yt.wait_for_caption_track(
        "vid1", fake, yt.QuotaLedger(), timeout_s=60, poll_s=0, sleep=appear_on_third_poll
    )
    assert track["id"] == "cap1"
    assert calls["n"] >= 2


def test_probe_tfmt_reports_the_value_the_api_actually_accepted():
    """ADR-000 gap G-2: tfmt has no enum, so the accepted value is observed, not assumed."""
    fake = FakeYouTube(caption_bodies={"cap1": b"WEBVTT\n"}, accepted_tfmt="vtt")
    ledger = yt.QuotaLedger()
    accepted, payload = yt.probe_tfmt("cap1", fake, ledger)
    assert accepted == "vtt"
    assert payload == b"WEBVTT\n"
    assert ledger.calls["captions.download"] == 2  # srt rejected, then vtt accepted


def test_probe_tfmt_raises_when_nothing_is_accepted():
    fake = FakeYouTube(accepted_tfmt="nope")
    with pytest.raises(yt.YouTubeError, match="no tfmt value was accepted"):
        yt.probe_tfmt("cap1", fake, yt.QuotaLedger())


# ------------------------------------------------- the A-04 interface promise: one shape

def test_corpus_and_youtube_sources_are_the_same_shape(sandbox):
    """A-04's `scan` must consume either source without branching on which it got."""
    videos = fake_uploads(sandbox)
    yt.write_video_ids(yt.match_videos_to_corpus(videos, sandbox), sandbox)

    fake = FakeYouTube(
        channels=CHANNEL_OK,
        playlist_items={"items": [{"contentDetails": {"videoId": v["video_id"]}} for v in videos]},
        videos={"items": [
            {"id": v["video_id"],
             "snippet": {"title": v["title"], "description": "d", "publishedAt": "2024-01-01T00:00:00Z"},
             "contentDetails": {"duration": "PT30S", "caption": "true"}}
            for v in videos
        ]},
        captions=[{"id": "cap1", "snippet": {"language": "en", "trackKind": "standard"}}],
        caption_bodies={"cap1": SRT_ONE.encode("utf-8")},
    )

    from almanac.extract import Locator, Source

    from_corpus = yt.iter_sources("corpus", corpus_dir=sandbox)
    from_youtube = yt.read_youtube_sources(fake, yt.QuotaLedger())

    assert len(from_corpus) == len(from_youtube) == 5
    for source in from_corpus + from_youtube:
        # A-03's own type, not a parallel one — A-04 must not have two shapes to reconcile.
        assert isinstance(source, Source)
        assert source.source_id and source.text and source.locators
        assert all(isinstance(locator, Locator) for locator in source.locators)
    assert {s.source_id for s in from_youtube} == {v["video_id"] for v in videos}


def test_iter_sources_rejects_an_unknown_origin():
    with pytest.raises(ValueError, match="unknown source"):
        yt.iter_sources("vimeo")


def test_locators_carry_timestamps_so_a_verdict_can_point_at_one():
    source = yt.source_from_srt("vid1", SRT_ONE)
    assert [locator.label for locator in source.locators] == ["00:00:00,000", "00:00:03,000"]
    first = source.locators[0]
    assert "23000" in source.text[first.start:first.end]


def test_a_downloaded_caption_flattens_exactly_as_the_corpus_reader_would(sandbox):
    """The guarantee behind the deferred proof: one flattener, not two that agree for now."""
    from almanac.extract import read_srt

    slug = sorted(p.name for p in sandbox.iterdir() if p.is_dir())[0]
    path = sandbox / slug / "captions.srt"
    from_disk = read_srt(path)
    as_if_downloaded = yt.source_from_srt("vid1", path.read_text())
    assert as_if_downloaded.text == from_disk.text
    assert as_if_downloaded.locators == from_disk.locators


# --------------------------------------------------- the live runner, exercised offline

class MultiCaptionFake(FakeYouTube):
    """Serves a different caption body per video, so a whole scan can be driven offline."""

    def __init__(self, srt_by_video, **kwargs):
        super().__init__(**kwargs)
        self.srt_by_video = srt_by_video

    def captions(self):
        outer = self

        class _Captions:
            def list(self, **kw):
                outer.seen.append(("captions.list", kw))
                return _Request({"items": [{
                    "id": "cap-" + kw["videoId"],
                    "snippet": {"language": "en", "trackKind": "standard"},
                }]})

            def download(self, **kw):
                outer.seen.append(("captions.download", kw))
                if kw.get("tfmt") != outer.accepted_tfmt:
                    return _Request(RuntimeError(f"HTTP 400: bad tfmt {kw.get('tfmt')!r}"))
                return _Request(outer.srt_by_video[kw["id"].replace("cap-", "")].encode("utf-8"))

        return _Captions()


def test_the_live_proof_runner_passes_end_to_end_offline(sandbox, tmp_path, monkeypatch):
    """Drives scripts/check_youtube.py's main() with a fake channel.

    The live run happens once, against Zaeem's real uploads, and a NameError discovered at
    that moment costs a round-trip through a human. This exercises the whole runner —
    listing, id write-back, the G-2 tfmt probe, all five downloads, every proof and the
    proof-file append — before it is ever pointed at the network.
    """
    videos = fake_uploads(sandbox)
    slugs = sorted(p.name for p in sandbox.iterdir() if p.is_dir())
    srt_by_video = {
        v["video_id"]: (sandbox / slug / "captions.srt").read_text()
        for v, slug in zip(videos, slugs)
    }

    fake = MultiCaptionFake(
        srt_by_video,
        channels=CHANNEL_OK,
        playlist_items={"items": [{"contentDetails": {"videoId": v["video_id"]}} for v in videos]},
        videos={"items": [
            {"id": v["video_id"],
             "snippet": {"title": v["title"], "description": "d",
                         "publishedAt": "2024-01-01T00:00:00Z"},
             "contentDetails": {"duration": "PT30S", "caption": "true"}}
            for v in videos
        ]},
    )
    proof_path = tmp_path / "A-06.md"
    monkeypatch.setattr(cy, "PROOF_PATH", proof_path)
    monkeypatch.setattr(cy, "build_client", lambda *a, **k: fake)

    assert cy.main([]) == 0

    written = proof_path.read_text()
    assert written.count("= PASS") == 3
    assert "= DEFERRED" in written
    # The quota line is arithmetic, so it must reproduce exactly.
    assert "1253" in written


def test_the_runner_refuses_to_write_a_proof_from_a_partial_channel(sandbox, monkeypatch, tmp_path):
    """Four uploads is not five. It must not produce a proof file at all."""
    videos = fake_uploads(sandbox)[:4]
    fake = MultiCaptionFake(
        {},
        channels=CHANNEL_OK,
        playlist_items={"items": [{"contentDetails": {"videoId": v["video_id"]}} for v in videos]},
        videos={"items": [
            {"id": v["video_id"],
             "snippet": {"title": v["title"], "description": "d", "publishedAt": ""},
             "contentDetails": {"duration": "PT30S", "caption": "true"}}
            for v in videos
        ]},
    )
    proof_path = tmp_path / "A-06.md"
    monkeypatch.setattr(cy, "PROOF_PATH", proof_path)
    monkeypatch.setattr(cy, "build_client", lambda *a, **k: fake)

    assert cy.main([]) == 2
    assert not proof_path.exists()
