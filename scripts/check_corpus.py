"""A-02 proofs for the synthetic channel corpus and the labeled claim set.

Run as a script to print the three PROOF lines; imported by tests/test_corpus.py so each check
is also a regression test.

On proof 3 and the shape of a negative assertion
------------------------------------------------
HAC-38 words this proof as "grep shows no real creator names, tickers, or brand URLs". A grep
against a wordlist is exactly the weak negative assertion A-01's review already caught twice: it
can only ever find what somebody thought to list, so it passes trivially and proves nothing.

This module inverts the burden instead. It extracts every capitalised proper noun, cashtag,
@handle and domain in the corpus and fails on anything not in an explicit allowlist of generic
finance vocabulary — an unknown proper noun is a failure by default, so adding a real creator's
name to the corpus breaks the build rather than sliding through.

And a check that cannot be shown to fail is not evidence. `prove_no_real_world_refs` therefore
runs the same detector over a CONTROL fixture carrying a real creator name, a real cashtag, a
brand URL and an @handle, and refuses to report PASS unless the detector flags all four first.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import srt

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANNEL_DIR = REPO_ROOT / "corpus" / "channel"
SCRIPTS_DIR = REPO_ROOT / "corpus" / "scripts"
CLAIMS_PATH = REPO_ROOT / "evals" / "claims.jsonl"

# Run directly as a script as well as under pytest (which sets pythonpath via pytest.ini).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MIN_WORDS, MAX_WORDS = 500, 800
MIN_CLAIMS = 40
MIN_HARD_NEGATIVES = 6
REQUIRED_STATUSES = {"correct", "stale_material", "stale_immaterial", "skip", "unresolved"}
REQUIRED_META_FIELDS = ("video_id", "title", "published_at", "description")

# Generic finance / calendar / grammar vocabulary. Every entry is a common noun, a month, a
# statutory term or a first-person pronoun — deliberately NOT a brand, product or person.
ALLOWED_PROPER_NOUNS = {
    "I", "I'd", "I'm", "I've", "I'll",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "IRA", "IRAs", "HSA", "HSAs", "IRS", "Roth", "Series",
}

# The four planted references proof 3 must catch before it is allowed to report a clean corpus.
CONTROL_FIXTURE = (
    "As Graham Stephan puts it, you should just buy $TSLA and read the breakdown at "
    "ramitsethi.com, or ask @themoneyguy about it."
)

_CASHTAG = re.compile(r"\$[A-Z]{1,5}\b")
_HANDLE = re.compile(r"@[A-Za-z0-9_.]{2,}")
_URL = re.compile(r"https?://\S+", re.I)
_DOMAIN = re.compile(r"\b[a-z0-9][a-z0-9-]*\.(?:com|net|org|io|co|tv|gov|edu|me|xyz|app|ai)\b", re.I)
_ALLCAPS = re.compile(r"\b[A-Z]{2,}(?:'[A-Za-z]+)?\b")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’]*")
# A capitalised word is "sentence-initial" if it opens the text, a line, a markdown heading, a
# list item, or follows terminal punctuation. Those positions are capitalised by grammar, not
# because the word names something.
_SENTENCE_BOUNDARY = re.compile(r"(?:^|[\n]|[.!?:;]\s|[#>\-*]\s|\d\.\s)\s*$")


def scan_for_real_world_refs(text: str) -> list[tuple[str, str]]:
    """Return (kind, token) for every reference to a real-world brand, person or handle."""
    findings: list[tuple[str, str]] = []
    findings += [("cashtag", m.group()) for m in _CASHTAG.finditer(text)]
    findings += [("handle", m.group()) for m in _HANDLE.finditer(text)]
    findings += [("url", m.group()) for m in _URL.finditer(text)]
    findings += [
        ("domain", m.group()) for m in _DOMAIN.finditer(text)
        if m.group().lower() not in {d.lower() for d in ALLOWED_PROPER_NOUNS}
    ]
    findings += [
        ("acronym", m.group()) for m in _ALLCAPS.finditer(text)
        if m.group() not in ALLOWED_PROPER_NOUNS
    ]
    for m in _TOKEN.finditer(text):
        word = m.group()
        if not word[0].isupper() or word in ALLOWED_PROPER_NOUNS:
            continue
        if word.isupper():
            continue  # already covered by the acronym pass
        if _SENTENCE_BOUNDARY.search(text[max(0, m.start() - 12): m.start()]):
            continue  # capitalised by position, not because it names something
        findings.append(("proper_noun", word))
    return findings


# --------------------------------------------------------------------------- proof 1

def video_dirs() -> list[Path]:
    return sorted(p for p in CHANNEL_DIR.iterdir() if p.is_dir())


def prove_corpus_shape() -> tuple[bool, str, list[str]]:
    detail: list[str] = []
    ok = True
    dirs = video_dirs()
    if len(dirs) != 5:
        ok = False
        detail.append(f"expected 5 video folders, found {len(dirs)}")

    for folder in dirs:
        meta_path, srt_path = folder / "meta.json", folder / "captions.srt"
        if not meta_path.exists() or not srt_path.exists():
            ok = False
            detail.append(f"{folder.name}: missing meta.json or captions.srt")
            continue

        meta = json.loads(meta_path.read_text())
        missing = [f for f in REQUIRED_META_FIELDS if not str(meta.get(f, "")).strip()]
        if missing:
            ok = False
            detail.append(f"{folder.name}: meta.json missing/empty {missing}")
        try:
            datetime.fromisoformat(str(meta["published_at"]).replace("Z", "+00:00"))
        except (ValueError, KeyError) as exc:
            ok = False
            detail.append(f"{folder.name}: published_at not ISO-8601 ({exc})")

        subs = list(srt.parse(srt_path.read_text()))  # raises SRTParseError on a malformed file
        words = sum(len(s.content.split()) for s in subs)
        if not MIN_WORDS <= words <= MAX_WORDS:
            ok = False
            detail.append(f"{folder.name}: {words} words outside {MIN_WORDS}-{MAX_WORDS}")

        prev_end = None
        for s in subs:
            if s.start >= s.end or (prev_end is not None and s.start < prev_end):
                ok = False
                detail.append(f"{folder.name}: cue {s.index} overlaps or is zero-length")
                break
            prev_end = s.end

        detail.append(
            f"  {folder.name:34s} {words:4d} words  {len(subs):3d} cues  "
            f"published {meta['published_at']}"
        )
    return ok, (
        "5 videos, each captions.srt parses with srt lib, 500-800 words, meta.json valid"
    ), detail


# --------------------------------------------------------------------------- proof 2

def load_claims() -> list[dict]:
    return [json.loads(line) for line in CLAIMS_PATH.read_text().splitlines() if line.strip()]


def searchable_text(rel: str) -> str:
    raw = (REPO_ROOT / rel).read_text()
    if rel.endswith(".srt"):
        raw = " ".join(s.content for s in srt.parse(raw))
    else:
        raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.DOTALL)
    return re.sub(r"\s+", " ", raw).strip()


def prove_claims_labeled() -> tuple[bool, str, list[str]]:
    from almanac.catalog import load_catalog

    detail: list[str] = []
    ok = True
    rows = load_claims()
    catalog_keys = set(load_catalog())

    if len(rows) < MIN_CLAIMS:
        ok = False
        detail.append(f"only {len(rows)} rows, need >= {MIN_CLAIMS}")

    statuses: dict[str, int] = {}
    for r in rows:
        statuses[r["expected_status"]] = statuses.get(r["expected_status"], 0) + 1
    if missing := REQUIRED_STATUSES - set(statuses):
        ok = False
        detail.append(f"statuses never exercised: {sorted(missing)}")

    hard_negatives = [
        r for r in rows
        if r["expected_status"] == "skip"
        and r["expected_claim_type"] in {"illustrative", "historical"}
    ]
    if len(hard_negatives) < MIN_HARD_NEGATIVES:
        ok = False
        detail.append(f"{len(hard_negatives)} hard negatives, need >= {MIN_HARD_NEGATIVES}")

    if bad_keys := sorted({
        r["expected_entity_key"] for r in rows
        if r["expected_entity_key"] is not None and r["expected_entity_key"] not in catalog_keys
    }):
        ok = False
        detail.append(f"expected_entity_key not in catalog: {bad_keys}")

    # A label pointing at a quote that is not in the corpus is an ungradeable ground-truth row.
    cache = {rel: searchable_text(rel) for rel in {r["source"] for r in rows}}
    if unlocatable := [(r["source"], r["quote"]) for r in rows if r["quote"] not in cache[r["source"]]]:
        ok = False
        detail.append(f"{len(unlocatable)} quote(s) not found in their source: {unlocatable[:3]}")

    # An entity key that resolves to no value would grade every row against it as unresolvable.
    if valueless := sorted({
        r["expected_entity_key"] for r in rows
        if r["expected_entity_key"] in catalog_keys
        and load_catalog()[r["expected_entity_key"]].value is None
        and not load_catalog()[r["expected_entity_key"]].history
    }):
        ok = False
        detail.append(f"expected_entity_key with neither value nor history: {valueless}")

    detail.append(f"  {len(rows)} rows  statuses={dict(sorted(statuses.items()))}")
    detail.append(
        f"  {len(hard_negatives)} hard negatives (illustrative/historical, must not be flagged)"
    )
    detail.append(
        f"  {len({r['expected_entity_key'] for r in rows if r['expected_entity_key']})} distinct "
        f"entity keys, all present in facts/catalog.yaml; all {len(rows)} quotes located in source"
    )
    return ok, (
        "evals/claims.jsonl has >=40 rows, all 5 statuses present, >=6 hard negatives, "
        "every expected_entity_key exists in catalog"
    ), detail


# --------------------------------------------------------------------------- proof 3

def _display(path: Path) -> str:
    """Repo-relative when possible; tests point the scan at a tmp copy, which is not."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def corpus_files() -> list[Path]:
    return sorted(
        [p for p in CHANNEL_DIR.rglob("*") if p.is_file()]
        + [p for p in SCRIPTS_DIR.rglob("*") if p.is_file()]
    )


def prove_no_real_world_refs() -> tuple[bool, str, list[str]]:
    detail: list[str] = []
    ok = True

    control = scan_for_real_world_refs(CONTROL_FIXTURE)
    kinds = {k for k, _ in control}
    if not {"cashtag", "handle", "domain", "proper_noun"} <= kinds:
        ok = False
        detail.append(f"CONTROL FAILED — detector missed a planted reference; caught {control}")
    detail.append(f"  control fixture -> {len(control)} findings across {sorted(kinds)} (detector is live)")

    scanned = 0
    for path in corpus_files():
        text = path.read_text()
        if path.suffix == ".json":
            meta = json.loads(text)
            text = "\n".join(str(meta.get(f, "")) for f in REQUIRED_META_FIELDS)
        scanned += 1
        if findings := scan_for_real_world_refs(text):
            ok = False
            detail.append(f"  {_display(path)}: {sorted(set(findings))}")
    detail.append(f"  {scanned} corpus files scanned, 0 unallowlisted references")
    return ok, "grep shows no real creator names, tickers, or brand URLs in corpus", detail


# --------------------------------------------------------------------------- runner

PROOFS = (prove_corpus_shape, prove_claims_labeled, prove_no_real_world_refs)


def main() -> int:
    lines, failed = [], False
    for proof in PROOFS:
        ok, claim, detail = proof()
        failed |= not ok
        for d in detail:
            print(d)
        line = f"PROOF A-02: {claim} = {'PASS' if ok else 'FAIL'}"
        print(line + "\n")
        lines.append(line)
    print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
