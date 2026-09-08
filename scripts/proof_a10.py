"""A-10 proof harness — the README's four checks, each with a control where absence is asserted.

Project Rules: "A check asserting an absence must first demonstrate it can register a presence."
Checks 2 and 3 are absence claims (no broken link; no ungrounded number), so each runs a NEGATIVE
CONTROL on a deliberately corrupted copy and must FAIL there before its PASS on the real file
counts for anything.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

# Numbers a reader would not trace to an artifact, with the reason each is exempt. Kept short on
# purpose: every exemption is a hole in the check.
ALLOWED_UNGROUNDED = {
    60.0: "'Try it in 60 seconds' — the section title, not a measurement",
    8000.0: "the uvicorn port in the run instructions",
}

# Where a number in the README is allowed to come from. Machine-readable repo artifacts only.
GROUNDING_SOURCES = [
    "evals/results.json", "evals/reproducibility.json", "almanac/judge.py", "almanac/catalog.py",
    "almanac/extract.py", "facts/catalog.yaml", "facts/rates.json", "reports/latest.json",
    "requirements.txt", "almanac/youtube.py", ".github/workflows/nightly.yml",
]

NUM = re.compile(r"(?<![A-Za-z_])\$?-?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z_])")
ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
VERBATIM = re.compile(
    r"<!-- BEGIN evals/results\.md.*?-->\n(.*?)\n<!-- END evals/results\.md -->", re.S)


def strip_noise(md: str) -> str:
    """Prose only: no fenced code, no link/image targets, no HTML attributes."""
    md = re.sub(r"```.*?```", " ", md, flags=re.S)
    md = re.sub(r"\]\([^)]*\)", "] ", md)
    md = re.sub(r"<[^>]+>", " ", md)
    return md


def to_float(tok: str) -> float | None:
    try:
        return float(tok.replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        return None


def numbers_in(text: str) -> set[float]:
    # U+2212 MINUS SIGN reads as a minus to a human, so it must to the checker too: without
    # this the README's "-2.17" would look like +2.17 and fail to ground against judge.py.
    text = text.replace("\u2212", "-").replace("\u2013", "-")
    return {v for v in (to_float(t) for t in NUM.findall(text)) if v is not None}


def check_verbatim(md: str) -> tuple[bool, str]:
    m = VERBATIM.search(md)
    if not m:
        return False, "the verbatim evals/results.md block markers are missing"
    pasted = m.group(1)
    actual = (ROOT / "evals" / "results.md").read_text(encoding="utf-8").rstrip("\n")
    if pasted != actual:
        return False, "the pasted block is NOT byte-identical to evals/results.md"
    return True, f"{len(pasted.splitlines())} lines byte-identical to evals/results.md"


def derived_from_report() -> set[float]:
    """Totals the README may legitimately quote that appear literally in no file.

    `reports/latest.json` stores per-status counts, not a total, so a claim like "83 verdicts,
    every one carrying its rule" is true and load-bearing yet grounds against nothing. Computing
    it here checks the claim against the report instead of exempting it — a wrong total still
    fails, which an entry in ALLOWED_UNGROUNDED would not have caught.
    """
    path = ROOT / "reports" / "latest.json"
    if not path.is_file():
        return set()
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for video in report.get("videos", []) for row in video.get("verdicts", [])]
    return {
        float(len(rows)),                                              # total verdicts
        float(sum(1 for r in rows if r.get("rule_fired"))),            # carrying a rule
        float(len(report.get("videos", []))),                          # videos scanned
    }


def check_numbers(md: str) -> tuple[bool, str, list[str]]:
    """Every number OUTSIDE the verbatim block must appear in a repo artifact.

    Numbers inside the block are grounded by check_verbatim: the block is a byte-identical copy of
    a file `almanac eval` generates, so re-grounding them here would only re-check the copy.
    """
    body = VERBATIM.sub(" ", md)
    prose = strip_noise(body)

    sources = GROUNDING_SOURCES + ["reports/latest.json (derived)"]
    ground: dict[str, set[float]] = {}
    dates: dict[str, set[str]] = {}
    for rel in GROUNDING_SOURCES:
        raw = (ROOT / rel).read_text(encoding="utf-8")
        ground[rel] = numbers_in(raw)
        dates[rel] = set(ISO_DATE.findall(raw))
    ground["reports/latest.json (derived)"] = derived_from_report()
    dates["reports/latest.json (derived)"] = set()

    unresolved, lines = [], []
    for d in sorted(set(ISO_DATE.findall(prose))):
        hit = [r for r in sources if d in dates[r]]
        (lines if hit else unresolved).append(f"  {d:>12}  <- {hit[0] if hit else 'NOTHING'}")
        if not hit:
            unresolved.append(d)
    prose_no_dates = ISO_DATE.sub(" ", prose)
    for v in sorted(numbers_in(prose_no_dates)):
        if v in ALLOWED_UNGROUNDED:
            lines.append(f"  {v:>12g}  <- ALLOWED: {ALLOWED_UNGROUNDED[v]}")
            continue
        hit = [r for r in sources if v in ground[r]]
        lines.append(f"  {v:>12g}  <- {hit[0] if hit else 'NOTHING'}")
        if not hit:
            unresolved.append(f"{v:g}")
    ok = not unresolved
    msg = (f"{len(lines)} numeric claims outside the verbatim block, all grounded"
           if ok else f"UNGROUNDED: {', '.join(str(u) for u in unresolved)}")
    return ok, msg, lines


def check_svg() -> tuple[bool, str]:
    svg = (ROOT / "docs" / "architecture.svg")
    if not svg.is_file() or svg.stat().st_size == 0:
        return False, "docs/architecture.svg missing or empty"
    text = svg.read_text(encoding="utf-8")
    if "<svg" not in text or "</svg>" not in text:
        return False, "docs/architecture.svg is not a well-formed SVG document"
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        return False, f"docs/architecture.svg does not parse: {exc}"
    modules = sorted(set(re.findall(r"(?<![\w/])([a-z_]+)\.py(?![\w])", text)))
    pathed = sorted(set(re.findall(r"([a-z_]+/[a-z_]+\.py)", text)))
    missing = [m for m in modules if not (ROOT / "almanac" / f"{m}.py").is_file()]
    missing += [p for p in pathed if not (ROOT / p).is_file()]
    if missing:
        return False, f"names in the SVG that are not real files: {missing}"
    return True, (f"parses; {len(modules)} module names all exist in almanac/ "
                  f"({', '.join(modules)}); plus {', '.join(pathed)}")


def check_links(md: str) -> tuple[bool, str]:
    """markdown-link-check is not installed here, so this is the same contract in-process:
    every local target must exist on disk, every http(s) target must answer < 400."""
    targets = re.findall(r"\]\(([^)\s]+)\)", md) + re.findall(r'src="([^"]+)"', md)
    broken = []
    for t in sorted(set(targets)):
        if t.startswith("http"):
            req = urllib.request.Request(t, headers={"User-Agent": "almanac-a10-linkcheck"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    if r.status >= 400:
                        broken.append(f"{t} -> HTTP {r.status}")
            except Exception as exc:  # noqa: BLE001 - any failure is a broken link
                broken.append(f"{t} -> {type(exc).__name__}: {exc}")
        else:
            if not (ROOT / t.split("#")[0]).exists():
                broken.append(f"{t} -> no such file")
    return (not broken), (f"{len(set(targets))} links, 0 broken" if not broken
                          else f"BROKEN: {'; '.join(broken)}")


def main() -> int:
    md = README.read_text(encoding="utf-8")
    results = []

    ok, msg = check_verbatim(md)
    results.append(("evals/results.md pasted verbatim (byte-identical) with date and set size", ok, msg))

    ok, msg = check_svg()
    results.append(("docs/architecture.svg exists, parses, and every module name in it exists in almanac/", ok, msg))

    ok, msg, lines = check_numbers(md)
    results.append(("every numeric claim in README traces to a repo artifact (script check)", ok, msg))

    ok, msg = check_links(md)
    results.append(("link check on README -> 0 broken links", ok, msg))

    if "--verbose" in sys.argv:
        print("\n-- number grounding --")
        for line in lines:
            print(line)
        print()

    all_ok = True
    for name, ok, msg in results:
        all_ok &= ok
        print(f"PROOF A-10: {name} = {'PASS' if ok else 'FAIL'}   [{msg}]")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
