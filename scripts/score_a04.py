"""Score judge.py against the 54 human labels in evals/claims.jsonl.

Asserts the SIZE of what it checked. A-01 shipped a check that silently skipped 2 of 29 rows
while reporting PASS; a scorer that quietly drops rows would report a better number than it earned.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from almanac.extract import discover_sources, extract_sources, locate  # noqa: E402
from almanac.judge import judge  # noqa: E402

CLAIMS_PATH = REPO_ROOT / "evals/claims.jsonl"


def main() -> int:
    rows = [json.loads(l) for l in CLAIMS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    source_ids = sorted({r["source"] for r in rows})
    sources = [discover_sources(REPO_ROOT / sid)[0] for sid in source_ids]
    claims_by_source = extract_sources(sources)
    by_id = {s.source_id: s for s in sources}

    scored = 0
    uncovered = 0
    hits = 0
    confusion: collections.Counter = collections.Counter()
    fired: collections.Counter = collections.Counter()
    misses = []

    for row in rows:
        sid = row["source"]
        span = locate(by_id[sid].text, row["quote"])
        assert span is not None, f"labelled quote not found in its source: {row['quote'][:60]}"
        lo, hi = span

        claim = None
        for candidate in claims_by_source[sid]:
            start = by_id[sid].text.find(candidate.quote)
            if start >= 0 and start < hi and start + len(candidate.quote) > lo:
                claim = candidate
                break
        if claim is None:
            uncovered += 1
            continue

        verdict = judge(claim)
        assert verdict.rule_fired, "a verdict with no rule_fired"
        scored += 1
        fired[verdict.rule_fired] += 1
        expected = row["expected_status"]
        confusion[(expected, verdict.status)] += 1
        if verdict.status == expected:
            hits += 1
        else:
            misses.append((expected, verdict.status, verdict.rule_fired, row["quote"][:52], verdict.detail))

    assert scored + uncovered == len(rows), "a row was neither scored nor counted as uncovered"

    print(f"\nlabelled rows {len(rows)} | covered by extraction {scored} | uncovered {uncovered}")
    print(f"status agreement {hits}/{scored} ({hits/scored:.0%})\n")

    statuses = ["skip", "correct", "stale_material", "stale_immaterial", "unresolved"]
    print(f"{'expected / got':<18}" + "".join(f"{s:>18}" for s in statuses))
    for expected in statuses:
        cells = "".join(f"{confusion[(expected, got)] or '·':>18}" for got in statuses)
        print(f"{expected:<18}{cells}")

    if misses:
        print(f"\n{len(misses)} disagreement(s):")
        for expected, got, rule, quote, detail in misses:
            print(f"  expected {expected:<17} got {got:<17} [{rule}]  {quote}\n      {detail}")
    else:
        print("\nno disagreements")

    print("\nrules fired:")
    for rule, n in sorted(fired.items(), key=lambda kv: -kv[1]):
        print(f"  {rule:<34} {n:>3}")
    return 0 if hits == scored else 1


if __name__ == "__main__":
    raise SystemExit(main())
