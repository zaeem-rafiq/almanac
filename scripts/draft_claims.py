"""Draft evals/claims.jsonl and verify every quote is really present in its source.

The agent DRAFTS these rows; Zaeem confirms or edits every `expected_*` field, after which
`evals/claims.jsonl` is protected (Project Rules).

A label is worthless if the quote it points at does not exist in the corpus — A-05 grades against
this file, so a typo'd quote becomes a permanently unfindable ground-truth row. This script
therefore refuses to write unless every quote is an exact substring of its source's normalised
text, and reports the offenders when it is not.

claim_type vocabulary (superset of models.Kind, which describes *facts*; these describe *claims*):
  statutory_limit  a dollar/threshold figure set by statute or regulation
  market_rate      a published market or programme rate
  illustrative     a hypothetical the creator made up to carry an example  -> never flagged
  historical       an explicitly past-tense figure, true of its own moment -> never flagged
  structural       a product rule stated as a number (lockups, penalties)  -> never flagged
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import srt

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "evals" / "claims.jsonl"

V1 = "corpus/channel/v1-401k-limits-explained/captions.srt"
V2 = "corpus/channel/v2-mortgage-or-invest/captions.srt"
V3 = "corpus/channel/v3-hsa-triple-tax-advantage/captions.srt"
V4 = "corpus/channel/v4-how-id-invest-10000/captions.srt"
V5 = "corpus/channel/v5-ibonds-vs-high-yield-savings/captions.srt"
OK = "corpus/scripts/fresh_ok.md"
WRONG = "corpus/scripts/fresh_wrong.md"

# (source, quote, claim_type, entity_key, status)
ROWS: list[tuple[str, str, str, str | None, str]] = [
    # ---------------------------------------------------------------- V1, published 2024-02
    (V1, "you can defer twenty three thousand dollars of your own money into your 401k",
     "statutory_limit", "k401_employee_deferral", "stale_material"),
    (V1, "the catch up is seven thousand five hundred dollars",
     "statutory_limit", "k401_catchup_50plus", "stale_material"),
    (V1, "The combined limit, yours plus theirs, is sixty nine thousand dollars",
     "statutory_limit", None, "unresolved"),
    (V1, "If you earn more than one hundred fifty five thousand dollars you are a highly compensated employee",
     "statutory_limit", None, "unresolved"),
    (V1, "Say you make seventy thousand dollars", "illustrative", None, "skip"),
    (V1, "your plan matches six percent of your salary", "illustrative", None, "skip"),
    (V1, "Six percent is four thousand two hundred dollars of your own money",
     "illustrative", None, "skip"),
    (V1, "If you dump five hundred dollars a month in January", "illustrative", None, "skip"),
    (V1, "Say you are in the twenty two percent bracket", "illustrative", None, "skip"),

    # ---------------------------------------------------------------- V2, published 2025-06
    (V2, "the thirty year fixed average is six point eight five percent",
     "market_rate", "mortgage_30y_fixed", "stale_immaterial"),
    (V2, "say you assume seven percent from a broad market index fund",
     "illustrative", None, "skip"),
    (V2, "Say you have three hundred thousand dollars left on the loan",
     "illustrative", None, "skip"),
    (V2, "your payment is about one thousand two hundred dollars a month",
     "illustrative", None, "skip"),
    (V2, "an extra five hundred dollars a month you could throw at either side",
     "illustrative", None, "skip"),
    (V2, "Fifteen years of extra payments applied to the wrong bucket",
     "illustrative", None, "skip"),

    # ---------------------------------------------------------------- V3, published 2025-01
    (V3, "you can put four thousand three hundred dollars in",
     "statutory_limit", "hsa_self_only", "stale_material"),
    (V3, "Family coverage is eight thousand five hundred fifty dollars",
     "statutory_limit", "hsa_family", "stale_material"),
    (V3, "the standard deduction for a single filer is fifteen thousand seven hundred fifty dollars",
     "statutory_limit", "standard_deduction_single", "stale_material"),
    (V3, "The annual gift exclusion is nineteen thousand dollars",
     "statutory_limit", "gift_annual_exclusion", "correct"),
    (V3, "the key employee threshold of two hundred thirty thousand dollars",
     "statutory_limit", None, "unresolved"),
    (V3, "Say your family puts in seven hundred dollars a month", "illustrative", None, "skip"),
    (V3, "say you are in the twenty four percent bracket", "illustrative", None, "skip"),
    (V3, "you can pull that money out of the account tax free in twenty years",
     "illustrative", None, "skip"),
    (V3, "Non medical withdrawals before sixty five get taxed and penalized",
     "structural", None, "skip"),

    # ---------------------------------------------------------------- V4, published 2026-03
    (V4, "Ten thousand dollars. It showed up, it is yours", "illustrative", None, "skip"),
    (V4, "Say you earn eighty thousand dollars", "illustrative", None, "skip"),
    (V4, "you have no debt above eight percent", "illustrative", None, "skip"),
    (V4, "Put one thousand dollars somewhere boring and instantly reachable",
     "illustrative", None, "skip"),
    (V4, "Whatever is left goes into three funds. Not thirty. Three.",
     "illustrative", None, "skip"),
    (V4, "Say you go sixty forty, sixty percent stocks and forty percent bonds",
     "illustrative", None, "skip"),
    (V4, "Or eighty twenty if you are younger", "illustrative", None, "skip"),
    (V4, "Say the whole thing returns seven percent a year", "illustrative", None, "skip"),
    (V4, "Over thirty years, at that assumed rate, a lump sum roughly doubles about three and a half times",
     "illustrative", None, "skip"),
    (V4, "Adding five hundred dollars a month to that same account", "illustrative", None, "skip"),

    # ---------------------------------------------------------------- V5, published 2024-11
    (V5, "The composite rate right now is three point one one percent",
     "market_rate", "ibond_composite_rate", "stale_material"),
    (V5, "Back in 2022 it paid nine point six two percent", "historical", None, "skip"),
    (V5, "it resets on the first of May and the first of November", "structural", None, "skip"),
    (V5, "You cannot touch the money for twelve months", "structural", None, "skip"),
    (V5, "If you cash out before five years you give up three months of interest",
     "structural", None, "skip"),
    (V5, "say you park ten thousand dollars", "illustrative", None, "skip"),

    # ---------------------------------------------------------------- fresh_ok.md (undated)
    (OK, "You can defer $24,500 into your 401(k) this year",
     "statutory_limit", "k401_employee_deferral", "correct"),
    (OK, "there is a catch-up of $8,000 on top of it",
     "statutory_limit", "k401_catchup_50plus", "correct"),
    (OK, "the IRA limit is $7,500", "statutory_limit", "ira_contribution", "correct"),
    (OK, "the combined employee-plus-employer ceiling is $72,000",
     "statutory_limit", None, "unresolved"),
    (OK, "the health savings account limit is $4,400",
     "statutory_limit", "hsa_self_only", "correct"),
    (OK, "Family coverage is $8,750", "statutory_limit", "hsa_family", "correct"),
    (OK, "The standard deduction for a single filer is $16,100",
     "statutory_limit", "standard_deduction_single", "correct"),
    (OK, "The annual gift exclusion is $19,000 per recipient",
     "statutory_limit", "gift_annual_exclusion", "correct"),
    (OK, "the 30-year fixed average is 6.71%", "market_rate", "mortgage_30y_fixed", "correct"),

    # ---------------------------------------------------------------- fresh_wrong.md (undated)
    (WRONG, "The limit is $7,000 this year",
     "statutory_limit", "ira_contribution", "stale_material"),
    (WRONG, "The 30-year fixed average is 6.85%",
     "market_rate", "mortgage_30y_fixed", "stale_immaterial"),
    (WRONG, "Say you make $50,000 and your plan matches the first 5%",
     "illustrative", None, "skip"),
    (WRONG, "That is $2,500 of somebody else's money per year", "illustrative", None, "skip"),
    (WRONG, "If you assume a 10% return on that", "illustrative", None, "skip"),
]


def searchable_text(rel: str) -> str:
    """Normalised text of a source, as a grader would search it.

    SRT sources collapse to their cue contents joined by single spaces, which is exactly what
    the words looked like before `build_corpus.py` chunked them. Markdown sources drop the
    provenance comment first, so a quote can never accidentally match the comment that
    documents it.
    """
    raw = (REPO_ROOT / rel).read_text()
    if rel.endswith(".srt"):
        raw = " ".join(s.content for s in srt.parse(raw))
    else:
        raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.DOTALL)
    return re.sub(r"\s+", " ", raw).strip()


def main() -> int:
    cache = {rel: searchable_text(rel) for rel in {r[0] for r in ROWS}}
    missing = [(rel, q) for rel, q, *_ in ROWS if q not in cache[rel]]
    if missing:
        print(f"REFUSING TO WRITE — {len(missing)} quote(s) not found in their source:")
        for rel, q in missing:
            print(f"  {rel}\n    {q!r}")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as fh:
        for rel, quote, ctype, key, status in ROWS:
            fh.write(json.dumps({
                "source": rel,
                "quote": quote,
                "expected_claim_type": ctype,
                "expected_entity_key": key,
                "expected_status": status,
            }) + "\n")

    counts: dict[str, int] = {}
    for *_, status in ROWS:
        counts[status] = counts.get(status, 0) + 1
    print(f"wrote {len(ROWS)} rows to {OUT.relative_to(REPO_ROOT)}; every quote located in source")
    print("  by status:", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
