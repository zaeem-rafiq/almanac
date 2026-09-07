"""A-06 proofs for YouTube ingest over owner OAuth.

Run as a script to print the PROOF lines; imported by tests/test_youtube.py so each check is
also a regression test.

SCOPE NOTE — one proof is deferred, deliberately
------------------------------------------------
HAC-42 lists four proofs. The third,

    PROOF A-06: scan --source youtube status counts == scan --source corpus status counts

needs a `scan --source {corpus,youtube}` command. A-04 has since landed `extract` and `judge`,
but no `scan` subcommand exists yet, so the comparison still cannot be run.
It is recorded in `docs/proofs/A-06.md` as DEFERRED and is **not** reported as passing here.
A-06 does not close on this run. Everything else in the issue is proven live.

On the shape of these checks
----------------------------
Two lessons from earlier issues are load-bearing here:

* A-01 shipped a url-200 check that silently skipped 2 of 29 rows and still printed PASS,
  because it keyed a dict per-entry instead of per-row. Proof 1 below therefore asserts the
  COUNT on both sides — five uploads on the channel, five corpus folders, five matches, no
  orphans — so a missing video cannot look identical to a matching one.

* A negative assertion proves nothing unless it can be shown to fail. Proof 2's "< 2% word
  diff" is run first against a deliberately corrupted caption and must come out RED before
  its green result on the real caption is reported. A threshold that cannot fail is not a
  threshold.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from almanac.cli import load_env  # noqa: E402
from almanac.youtube import (  # noqa: E402
    QuotaLedger,
    YouTubeError,
    build_client,
    source_from_srt,
    download_captions,
    list_channel_videos,
    match_videos_to_corpus,
    write_video_ids,
)

CHANNEL_DIR = REPO_ROOT / "corpus" / "channel"
PROOF_PATH = REPO_ROOT / "docs" / "proofs" / "A-06.md"
ADR_PATH = REPO_ROOT / "docs" / "decisions" / "ADR-000-stack.md"

EXPECTED_VIDEOS = 5
WORD_DIFF_THRESHOLD = 0.02
QUOTA_BUDGET = 1500

# A YouTube video id is 11 characters of the URL-safe base64 alphabet. Checking the shape is
# what stops a placeholder ("placeholder-v1-401k-limits") passing for a real id.
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_dirs() -> list[Path]:
    return sorted(p for p in CHANNEL_DIR.iterdir() if p.is_dir())


def _words(text: str) -> list[str]:
    """Words of the CAPTION BODY, lowercased and stripped of edge punctuation.

    The SRT scaffolding — cue indices and timestamps — is parsed away first. Leaving it in
    was a real bug caught by `test_the_adversarial_control_actually_goes_red`: "00:00:02,800"
    tokenises into several word-like fragments, and because those fragments are identical on
    both sides they pad the denominator with guaranteed matches. A caption with 3% of its
    real words rewritten scored 1.4% and slid under the 2% threshold. The comparator must
    measure only what Almanac actually reads.

    Edge punctuation is stripped but interior punctuation is kept, so "23,000" and "3.63"
    survive as single tokens. Those are the numbers this whole project exists to check; a
    tokeniser that split them would compare everything except the part that matters.

    SRT round-trips through YouTube with cosmetic differences — cue re-segmentation, casing
    at a boundary. Those are not content drift and must not fail the check.
    """
    try:
        # Flatten through the SAME reader the extractor uses, so the comparator measures the
        # text Almanac will actually read rather than an approximation of it.
        body = source_from_srt("cmp", text).text or text
    except Exception:
        body = text  # not parseable as SRT; compare it raw rather than crash the comparator
    tokens = (token.strip(".,;:!?()[]\"'") for token in body.lower().split())
    return [token for token in tokens if token]


def word_diff_ratio(left: str, right: str) -> float:
    """Fraction of words that do NOT align between two caption texts, in [0, 1]."""
    a, b = _words(left), _words(right)
    if not a and not b:
        return 0.0
    matched = sum(block.size for block in difflib.SequenceMatcher(None, a, b).get_matching_blocks())
    return 1.0 - matched / max(len(a), len(b))


def corrupt_captions(srt_text: str, every: int = 3) -> str:
    """Damage a caption file enough to push it past the threshold — the adversarial control.

    Replaces one word in every `every`-th cue. That is a few percent of the words, which is
    exactly the regime the 2% threshold is supposed to catch; a control that mangled the whole
    file would prove only that the comparator notices catastrophe.
    """
    import srt as srt_lib

    subs = list(srt_lib.parse(srt_text))
    for i, sub in enumerate(subs):
        if i % every == 0:
            words = sub.content.split()
            if words:
                words[0] = "ZZQXW"
                sub.content = " ".join(words)
    return srt_lib.compose(subs)


# ------------------------------------------------------------------ proof 1: five real ids

def prove_ids_match(videos: list[dict]) -> tuple[bool, str]:
    """Every corpus folder maps to exactly one upload, and no upload is left over.

    Counted on BOTH sides on purpose (see module docstring).
    """
    folders = video_dirs()
    problems = []

    if len(folders) != EXPECTED_VIDEOS:
        problems.append(f"corpus has {len(folders)} folders, expected {EXPECTED_VIDEOS}")
    if len(videos) != EXPECTED_VIDEOS:
        problems.append(f"channel has {len(videos)} uploads, expected {EXPECTED_VIDEOS}")

    try:
        mapping = match_videos_to_corpus(videos)
    except YouTubeError as exc:
        return False, str(exc)

    if len(mapping) != EXPECTED_VIDEOS:
        unmatched = sorted({f.name for f in folders} - set(mapping))
        problems.append(f"{len(mapping)}/{EXPECTED_VIDEOS} folders matched a title; unmatched={unmatched}")

    api_ids = {v["video_id"] for v in videos}
    mapped_ids = set(mapping.values())
    if len(mapped_ids) != len(mapping):
        problems.append("two corpus folders mapped to the same video id")
    orphans = sorted(api_ids - mapped_ids)
    if orphans:
        problems.append(f"uploads matched by no corpus folder: {orphans}")

    meta_ids = {}
    for folder in folders:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        meta_ids[folder.name] = meta.get("video_id", "")
        if not VIDEO_ID_RE.match(meta_ids[folder.name]):
            problems.append(f"{folder.name}: video_id {meta_ids[folder.name]!r} is not a real id")
        elif mapping.get(folder.name) and meta_ids[folder.name] != mapping[folder.name]:
            problems.append(
                f"{folder.name}: meta.json says {meta_ids[folder.name]} but the channel says "
                f"{mapping[folder.name]}"
            )

    detail = (
        f"channel={len(videos)} corpus={len(folders)} matched={len(mapping)} "
        f"orphans={len(orphans)}"
    )
    if problems:
        return False, detail + " | " + "; ".join(problems)
    return True, detail + " | " + ", ".join(f"{s}={i}" for s, i in sorted(mapping.items()))


# ---------------------------------------------------- proof 2: downloaded captions match

def prove_caption_fidelity(slug: str, downloaded_srt: str) -> tuple[bool, str]:
    """Downloaded captions parse as SRT and differ from the corpus by < 2% of words.

    The adversarial control runs FIRST. If a corrupted caption does not breach the threshold,
    the comparator is broken and the clean result is not evidence, so this reports FAIL even
    when the real diff looks perfect.
    """
    corpus_srt = (CHANNEL_DIR / slug / "captions.srt").read_text(encoding="utf-8")

    control_ratio = word_diff_ratio(corpus_srt, corrupt_captions(corpus_srt))
    if control_ratio <= WORD_DIFF_THRESHOLD:
        return False, (
            f"adversarial control did not go red: a corrupted caption scored "
            f"{control_ratio:.4%} <= {WORD_DIFF_THRESHOLD:.0%}. The comparator cannot fail, "
            "so its clean result proves nothing."
        )

    try:
        parsed = source_from_srt("downloaded", downloaded_srt)
    except Exception as exc:
        return False, f"downloaded caption did not parse as SRT: {type(exc).__name__}: {exc}"
    if not parsed.locators:
        return False, "downloaded caption parsed to zero cues"

    ratio = word_diff_ratio(corpus_srt, downloaded_srt)
    detail = (
        f"{slug} cues={len(parsed.locators)} word_diff={ratio:.4%} threshold={WORD_DIFF_THRESHOLD:.0%} "
        f"(control={control_ratio:.2%} RED as required)"
    )
    return ratio < WORD_DIFF_THRESHOLD, detail


# ------------------------------------------------------------------- proof 4: quota budget

def prove_quota(ledger: QuotaLedger) -> tuple[bool, str]:
    """Counted calls priced at documented unit costs — arithmetic, never an estimate.

    Call counts are OBSERVED by the ledger. Unit costs are DOCUMENTED (YouTube's quota table;
    the discovery document carries no cost metadata). The two halves are reported separately
    so neither is mistaken for the other.
    """
    detail = " | ".join(row.strip() for row in ledger.breakdown())
    return ledger.units < QUOTA_BUDGET, f"{detail} | budget={QUOTA_BUDGET}"


# ------------------------------------------------------------------------------ live runner

def _append_proofs(lines: list[str], sections: list[str]) -> None:
    PROOF_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with PROOF_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## Run {stamp}\n\n")
        for section in sections:
            handle.write(section.rstrip() + "\n\n")
        handle.write("```\n" + "\n".join(lines) + "\n```\n")


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    load_env()
    write_proof = "--no-proof" not in argv

    print("Almanac A-06 — YouTube ingest proofs\n" + "-" * 72)

    youtube = build_client()

    # --- listing -------------------------------------------------------------------------
    scan_ledger = QuotaLedger()
    videos = list_channel_videos(youtube, scan_ledger)
    print(f"  channel uploads: {len(videos)}")
    for video in videos:
        print(f"    {video['video_id']}  captions={video['caption']:<5} {video['title'][:52]}")

    if len(videos) < EXPECTED_VIDEOS:
        print(
            f"\n  Only {len(videos)} of {EXPECTED_VIDEOS} uploads are on the channel. "
            "Finish scripts/seed_channel.md and re-run — no proof is written from a partial "
            "channel."
        )
        return 2

    mapping = match_videos_to_corpus(videos)
    changed = write_video_ids(mapping)
    print(f"  meta.json video_id written for: {changed or 'nothing (already current)'}")

    ids_ok, ids_detail = prove_ids_match(videos)

    # --- gap G-2: is tfmt="srt" actually accepted? ----------------------------------------
    from almanac.youtube import probe_tfmt, wait_for_caption_track

    probe_ledger = QuotaLedger()
    first_slug = sorted(mapping)[0]
    first_id = mapping[first_slug]
    track = wait_for_caption_track(first_id, youtube, probe_ledger)
    accepted_tfmt, _ = probe_tfmt(track["id"], youtube, probe_ledger)
    g2_note = (
        f"tfmt={accepted_tfmt!r} accepted on {first_id} (track {track['id'][:12]}…, "
        f"trackKind={track['snippet'].get('trackKind')}, language={track['snippet'].get('language')})"
    )
    print(f"  G-2: {g2_note}")

    # --- full scan: captions for every video ----------------------------------------------
    downloaded: dict[str, str] = {}
    for slug, video_id in sorted(mapping.items()):
        downloaded[slug] = download_captions(
            video_id, youtube, scan_ledger, tfmt=accepted_tfmt
        )
        print(f"  downloaded captions: {slug} ({len(downloaded[slug])} bytes)")

    fidelity_ok, fidelity_detail = prove_caption_fidelity(first_slug, downloaded[first_slug])

    # Every video is compared, not only the one the proof line names — a per-video table is
    # what makes "one video matched" distinguishable from "all five matched".
    per_video = []
    for slug in sorted(downloaded):
        corpus_srt = (CHANNEL_DIR / slug / "captions.srt").read_text(encoding="utf-8")
        per_video.append((slug, word_diff_ratio(corpus_srt, downloaded[slug])))

    quota_ok, quota_detail = prove_quota(scan_ledger)

    lines = [
        f"PROOF A-06: list_channel_videos returns {EXPECTED_VIDEOS} ids matching corpus "
        f"meta.json = {'PASS' if ids_ok else 'FAIL'}",
        f"PROOF A-06: download_captions(V1) parses with srt lib and word-diff vs corpus "
        f"captions.srt < 2% = {'PASS' if fidelity_ok else 'FAIL'}",
        f"PROOF A-06: quota used for the full scan < {QUOTA_BUDGET:,} units "
        f"(5 x captions.download @200 + lists) = {'PASS' if quota_ok else 'FAIL'}",
        "PROOF A-06: scan --source youtube status counts == scan --source corpus status "
        "counts = DEFERRED (needs a `scan --source {corpus,youtube}` command; A-04 has "
        "landed extract/judge but no scan subcommand exists yet)",
    ]

    print("-" * 72)
    print(f"  ids       {ids_detail}")
    print(f"  fidelity  {fidelity_detail}")
    print(f"  quota     {quota_detail}")
    print("  per-video word diff vs corpus:")
    for slug, ratio in per_video:
        print(f"    {slug:<34} {ratio:.4%}")
    print("-" * 72)
    for line in lines:
        print(line)

    if write_proof:
        table = ["| corpus slug | video id | word diff vs corpus |", "|---|---|---|"]
        for slug, ratio in per_video:
            table.append(f"| `{slug}` | `{mapping[slug]}` | {ratio:.4%} |")
        _append_proofs(lines, [
            f"- `ids` — {ids_detail}",
            f"- `fidelity` — {fidelity_detail}",
            f"- `quota` — {quota_detail}",
            f"- `G-2` — {g2_note}; probe cost {probe_ledger.units} units",
            "\n".join(table),
        ])

    return 0 if (ids_ok and fidelity_ok and quota_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
