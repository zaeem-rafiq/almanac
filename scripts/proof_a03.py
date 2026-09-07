"""A-03 proof harness — runs the extractor live over the five-video channel corpus.

Prints each PROOF line to the transcript and appends the same lines to docs/proofs/A-03.md.

Two of these proofs are shaped by mistakes A-00 and A-01 already paid for:

  * Proof 2 is a NEGATIVE assertion ("V4 yields none of these types"). A negative assertion is
    worthless on its own — it passes trivially if extraction silently returned nothing. So it also
    asserts V4 returned claims, and that the three types it must not carry DID fire elsewhere in
    the same run. An extractor that returned [] now fails this proof instead of passing it.

  * Proof 4 iterates every claim across five videos. A-01 shipped a check that keyed its
    collection per-entry instead of per-row and silently skipped 2 of 29 URLs while reporting PASS.
    So proof 4 COUNTS what it checked and asserts the counts equal the claim total. A claim that
    never got checked can no longer look identical to one that passed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from almanac.catalog import load_catalog  # noqa: E402
from almanac.extract import discover_sources, extract_sources  # noqa: E402

PROOF_PATH = REPO_ROOT / "docs/proofs/A-03.md"
WALL_TIME_BUDGET_S = 90.0
VIDEOS = [
    "v1-401k-limits-explained",
    "v2-mortgage-or-invest",
    "v3-hsa-triple-tax-advantage",
    "v4-how-id-invest-10000",
    "v5-ibonds-vs-high-yield-savings",
]
JUDGED_TYPES = {"statutory_limit", "tax_bracket", "market_rate"}


def main() -> int:
    sources = [discover_sources(REPO_ROOT / "corpus/channel" / name)[0] for name in VIDEOS]
    assert len(sources) == 5, f"expected 5 videos, discovered {len(sources)}"
    by_id = {s.source_id: s for s in sources}

    started = time.monotonic()
    claims_by_source = extract_sources(sources)
    wall_time = time.monotonic() - started

    def claims_for(video: str) -> list:
        (source_id,) = [sid for sid in claims_by_source if video in sid]
        return claims_by_source[source_id]

    all_claims = [c for claims in claims_by_source.values() for c in claims]
    print(f"\nextracted {len(all_claims)} claim(s) from {len(sources)} video(s) in {wall_time:.1f}s")
    for name in VIDEOS:
        print(f"  {name:<34} {len(claims_for(name)):>3} claim(s)")

    lines: list[str] = []

    # ---------------------------------------------------------------------------- proof 1
    v1 = claims_for("v1-401k-limits-explained")
    hits = [
        c for c in v1
        if c.entity_key == "k401_employee_deferral"
        and c.claim_type == "statutory_limit"
        and c.value == 23_000.0
    ]
    detail = (
        f"{len(hits)} matching claim(s); "
        + (f'value={hits[0].value:,.0f} @ {hits[0].locator} "{hits[0].quote[:60]}"' if hits else "none")
    )
    lines.append(_line(
        "V1 yields >=1 claim with entity_key=k401_employee_deferral, claim_type=statutory_limit, "
        "value matches the script",
        bool(hits), detail,
    ))

    # ---------------------------------------------------------------------------- proof 2
    v4 = claims_for("v4-how-id-invest-10000")
    offenders = [c for c in v4 if c.claim_type in JUDGED_TYPES]
    # Adversarial probe: the three types must be PRODUCIBLE by this same run, or "zero on V4" is
    # only evidence that the labeller is broken.
    elsewhere = [
        c for sid, claims in claims_by_source.items() for c in claims
        if "v4-how-id-invest-10000" not in sid and c.claim_type in JUDGED_TYPES
    ]
    types_elsewhere = sorted({c.claim_type for c in elsewhere})
    proof2 = bool(v4) and not offenders and len(elsewhere) > 0
    lines.append(_line(
        "V4 yields 0 claims with claim_type in {statutory_limit, tax_bracket, market_rate}",
        proof2,
        f"V4 returned {len(v4)} claim(s), {len(offenders)} of those types "
        f"[{', '.join(sorted({c.claim_type for c in v4}))}]; adversarial probe: the same run "
        f"produced {len(elsewhere)} claim(s) of those types on other videos {types_elsewhere}",
    ))

    # ---------------------------------------------------------------------------- proof 3
    v5 = claims_for("v5-ibonds-vs-high-yield-savings")
    nine_six_two = [c for c in v5 if "nine point six two percent" in c.quote]
    proof3 = bool(nine_six_two) and all(c.claim_type == "historical" for c in nine_six_two)
    lines.append(_line(
        'V5 "9.62%" claim has claim_type=historical',
        proof3,
        f"{len(nine_six_two)} claim(s) quoting the 9.62% sentence, "
        f"claim_type(s)={sorted({c.claim_type for c in nine_six_two}) or 'NONE FOUND'}"
        + (f", value={nine_six_two[0].value}, year_hint={nine_six_two[0].year_hint}" if nine_six_two else ""),
    ))

    # ---------------------------------------------------------------------------- proof 4
    catalog_keys = set(load_catalog().keys())
    entity_checked = 0
    entity_bad: list[str] = []
    quote_checked = 0
    quote_bad: list[str] = []

    for claim in all_claims:
        entity_checked += 1
        if claim.entity_key is not None and claim.entity_key not in catalog_keys:
            entity_bad.append(f"{claim.source_id}:{claim.locator} -> {claim.entity_key!r}")

        quote_checked += 1
        source_text = by_id[claim.source_id].text
        if claim.quote not in source_text or len(claim.quote) > 200:
            quote_bad.append(f"{claim.source_id}:{claim.locator} -> {claim.quote[:50]!r}")

    total = len(all_claims)
    counts_agree = entity_checked == total and quote_checked == total and total > 0
    proof4 = counts_agree and not entity_bad and not quote_bad and wall_time < WALL_TIME_BUDGET_S
    lines.append(_line(
        "every returned entity_key is in catalog or None; every quote is a verbatim substring of "
        "the input; 5-video run wall time < 90s",
        proof4,
        f"{total} claim(s) total | entity_key checked {entity_checked}/{total}, "
        f"{len(entity_bad)} off-catalog | quote checked {quote_checked}/{total}, "
        f"{len(quote_bad)} not verbatim | wall time {wall_time:.1f}s < {WALL_TIME_BUDGET_S:.0f}s"
        + (f" | OFFENDERS {(entity_bad + quote_bad)[:3]}" if entity_bad or quote_bad else ""),
    ))

    for line in lines:
        print(line)
    _append(lines, total, wall_time)

    return 0 if all("= PASS" in line for line in lines) else 1


def _line(check: str, passed: bool, detail: str) -> str:
    return f"PROOF A-03: {check} = {'PASS' if passed else 'FAIL'}  [{detail}]"


def _append(lines: list[str], total: int, wall_time: float) -> None:
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = [
        f"\n## Run {stamp}",
        "",
        f"`venv/bin/python scripts/proof_a03.py` — {total} claim(s) from 5 videos in {wall_time:.1f}s.",
        "",
        "```",
        *lines,
        "```",
        "",
    ]
    PROOF_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PROOF_PATH.exists():
        PROOF_PATH.write_text("# A-03 — Claim extractor · proofs\n", encoding="utf-8")
    with PROOF_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(body))


if __name__ == "__main__":
    raise SystemExit(main())
