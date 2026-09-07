"""A-02 corpus and eval-set tests.

Each of the three PROOF checks gets two tests: one that it passes on the real corpus, and one
that it *fails* when the corpus is deliberately broken. A-01 shipped two checks that could not
be made to fail and therefore proved nothing; the negative half of each pair below is what makes
the positive half evidence rather than assertion.
"""

import json
import shutil

import pytest
import srt

from scripts import check_corpus as cc


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A writable copy of the corpus, wired into check_corpus's module-level paths."""
    channel = tmp_path / "channel"
    scripts = tmp_path / "scripts"
    shutil.copytree(cc.CHANNEL_DIR, channel)
    shutil.copytree(cc.SCRIPTS_DIR, scripts)
    claims = tmp_path / "claims.jsonl"
    claims.write_text(cc.CLAIMS_PATH.read_text())
    monkeypatch.setattr(cc, "CHANNEL_DIR", channel)
    monkeypatch.setattr(cc, "SCRIPTS_DIR", scripts)
    monkeypatch.setattr(cc, "CLAIMS_PATH", claims)
    return tmp_path


# ------------------------------------------------------------------ proof 1: corpus shape

def test_corpus_shape_passes():
    ok, _, detail = cc.prove_corpus_shape()
    assert ok, detail


def test_five_videos_with_the_required_publish_dates():
    dates = {
        json.loads((d / "meta.json").read_text())["published_at"][:7]
        for d in cc.video_dirs()
    }
    assert dates == {"2024-02", "2025-06", "2025-01", "2026-03", "2024-11"}


def test_every_caption_file_parses_and_is_monotonic():
    for folder in cc.video_dirs():
        subs = list(srt.parse((folder / "captions.srt").read_text()))
        assert subs, folder.name
        assert all(a.end <= b.start for a, b in zip(subs, subs[1:])), folder.name


def test_corpus_shape_fails_on_a_short_script(sandbox):
    victim = next(iter(cc.video_dirs())) / "captions.srt"
    subs = list(srt.parse(victim.read_text()))[:5]
    victim.write_text(srt.compose(subs))
    ok, _, detail = cc.prove_corpus_shape()
    assert not ok
    assert any("outside 500-800" in d for d in detail), detail


def test_corpus_shape_fails_on_unparseable_captions(sandbox):
    (next(iter(cc.video_dirs())) / "captions.srt").write_text("not an srt file at all\n")
    with pytest.raises(srt.SRTParseError):
        cc.prove_corpus_shape()


def test_corpus_shape_fails_on_empty_meta_field(sandbox):
    victim = next(iter(cc.video_dirs())) / "meta.json"
    meta = json.loads(victim.read_text())
    meta["published_at"] = ""
    victim.write_text(json.dumps(meta))
    ok, _, detail = cc.prove_corpus_shape()
    assert not ok
    assert any("missing/empty" in d for d in detail), detail


# ------------------------------------------------------------------ proof 2: labeled claims

def test_claims_labeled_passes():
    ok, _, detail = cc.prove_claims_labeled()
    assert ok, detail


def test_every_status_and_enough_hard_negatives():
    rows = cc.load_claims()
    assert len(rows) >= cc.MIN_CLAIMS
    assert {r["expected_status"] for r in rows} == cc.REQUIRED_STATUSES
    negatives = [
        r for r in rows
        if r["expected_status"] == "skip"
        and r["expected_claim_type"] in {"illustrative", "historical"}
    ]
    assert len(negatives) >= cc.MIN_HARD_NEGATIVES


def test_v4_is_entirely_hard_negatives():
    """The issue's sharpest requirement: V4 quotes only illustrative arithmetic."""
    rows = [r for r in cc.load_claims() if "v4-how-id-invest-10000" in r["source"]]
    assert rows
    assert {r["expected_status"] for r in rows} == {"skip"}
    assert {r["expected_entity_key"] for r in rows} == {None}


def test_v5_has_one_stale_material_and_one_historical_skip():
    rows = [r for r in cc.load_claims() if "v5-ibonds" in r["source"]]
    assert [r for r in rows if r["expected_status"] == "stale_material"]
    assert [r for r in rows if r["expected_claim_type"] == "historical"
            and r["expected_status"] == "skip"]


def test_labeled_entity_keys_resolve_through_the_catalog():
    """A key that exists but resolves to nothing would grade every row against it as junk."""
    from almanac.catalog import current, load_catalog

    catalog = load_catalog()
    for key in {r["expected_entity_key"] for r in cc.load_claims() if r["expected_entity_key"]}:
        assert key in catalog
        assert current(key).value is not None or catalog[key].history


def test_claims_fail_on_an_unknown_entity_key(sandbox):
    rows = cc.load_claims()
    rows[0]["expected_entity_key"] = "not_a_real_key"
    cc.CLAIMS_PATH.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    ok, _, detail = cc.prove_claims_labeled()
    assert not ok
    assert any("not in catalog" in d for d in detail), detail


def test_claims_fail_on_a_quote_that_is_not_in_the_corpus(sandbox):
    rows = cc.load_claims()
    rows[0]["quote"] = "a sentence that appears nowhere in any script"
    cc.CLAIMS_PATH.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    ok, _, detail = cc.prove_claims_labeled()
    assert not ok
    assert any("not found in their source" in d for d in detail), detail


def test_claims_fail_when_a_status_is_never_exercised(sandbox):
    rows = [r for r in cc.load_claims() if r["expected_status"] != "unresolved"]
    cc.CLAIMS_PATH.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    ok, _, detail = cc.prove_claims_labeled()
    assert not ok
    assert any("never exercised" in d for d in detail), detail


# ------------------------------------------------------------------ proof 3: no real-world refs

def test_no_real_world_refs_passes():
    ok, _, detail = cc.prove_no_real_world_refs()
    assert ok, detail


@pytest.mark.parametrize(
    "planted, kind",
    [
        ("as Graham Stephan explains", "proper_noun"),
        ("just buy $TSLA today", "cashtag"),
        ("read more at ramitsethi.com", "domain"),
        ("go to https://www.nerdwallet.com/article", "url"),
        ("ask @themoneyguy about it", "handle"),
        ("the VTSAX crowd will tell you", "acronym"),
    ],
)
def test_detector_catches_each_class_of_reference(planted, kind):
    assert kind in {k for k, _ in cc.scan_for_real_world_refs(planted)}, planted


def test_detector_fires_on_the_real_corpus_when_a_name_is_planted(sandbox):
    """The control fixture proves the detector works; this proves the *corpus scan* can fail.

    A check that only ever runs against clean input is not evidence, so the same planted name is
    written into a real corpus file and the whole proof must go red.
    """
    victim = cc.SCRIPTS_DIR / "fresh_ok.md"
    victim.write_text(victim.read_text() + "\nAs Graham Stephan says, buy $VOO at vanguard.com.\n")
    ok, _, detail = cc.prove_no_real_world_refs()
    assert not ok
    assert any("fresh_ok.md" in d for d in detail), detail


def test_allowlist_does_not_swallow_a_lookalike_brand():
    """`May` is allowlisted as a month; that must not extend to an unlisted capitalised word."""
    assert not cc.scan_for_real_world_refs("it resets in May and again in November")
    assert cc.scan_for_real_world_refs("it resets in Maybank and again in November")


def test_sentence_initial_capitals_are_not_flagged():
    """Grammar capitalises sentence openers; flagging those would make the check unusable."""
    assert not cc.scan_for_real_world_refs("Guaranteed is the word. Money in a brokerage helps.")
