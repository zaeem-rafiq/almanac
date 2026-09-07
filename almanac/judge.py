"""Decides whether a claim is stale. Rules R1-R5.

RULE: every verdict originates here and carries `rule_fired`. No model is called from this
module. A change that puts a verdict in a prompt is wrong.

`Verdict` lives here rather than in models.py: models.py locked at the end of A-03, and the
verdict shape belongs to the module that owns verdicts.

## The rules, in execution order — ordering IS the design

R1 historical_window     a claim the speaker placed in the past is resolved against the catalog's
                         HISTORY, never against today's value. Runs first, because it is the guard
                         that stops R5 from firing on an accurate sentence.
R2 unjudged_claim_type   illustrative / structural / other / unkeyed historical -> skip.
R3 no_resolvable_fact    a judged type Almanac's catalog cannot resolve -> unresolved.
R4 matches_current       claimed == current -> correct. Exact: the labels carry no tolerance band.
R5 diverges_from_current claimed != current -> stale, material by KIND (see MATERIALITY below).

## Why R1 runs first

"Back in 2022 it paid nine point six two percent" sits 125.8% away from today's I-bond composite
rate. Compared against `current`, it is the loudest divergence in the corpus — and it is a
completely accurate sentence. R1 resolves it against the 2022 window instead, finds 9.62, and
returns `skip`. A judge that reached R5 here would publish a confident false correction citing
treasurydirect.gov, which is the exact failure this product exists to prevent.

The converse matters just as much and is why R1 is narrow: "For 2024 you can defer twenty three
thousand dollars" also names a past year, but the speaker asserts it as the live limit, so it is a
`statutory_limit` and must be judged against today. Only a claim the model labelled `historical` —
the speaker framing it as past — routes to history. `year_hint` alone never does.

## MATERIALITY is per-kind, and that is measured, not assumed

Across the labelled set, `stale_material` spans -27.0%..-2.17% and `stale_immaterial` sits at
+2.09%. The bands OVERLAP 0.08 percentage points apart, so no single threshold reproduces them.
What separates them is `kind`:

  statutory_limit / tax_bracket   an exact-match domain. Any difference is material: a viewer who
                                  acts on 23,000 when the limit is 24,500 contributes the wrong
                                  amount, and the size of the gap does not change that.
  market_rate                     a drift domain. Rates move constantly and the video's argument
                                  survives a small move; only a large move invalidates it.

`kind` is read from facts/catalog.yaml, not from the model's `claim_type` — the code decides which
domain a fact lives in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from almanac.catalog import current, load_catalog, windows_for
from almanac.models import Claim, Fact

Status = Literal["skip", "correct", "stale_material", "stale_immaterial", "unresolved"]

JUDGED_CLAIM_TYPES = frozenset({"statutory_limit", "tax_bracket", "market_rate"})
EXACT_MATCH_KINDS = frozenset({"statutory_limit", "tax_bracket"})

# A market rate must move by at least this many percentage points to be worth a correction.
# Measured bound: the labelled set puts an immaterial move at 0.14pp and a material one at 1.15pp,
# so anything in that gap reproduces the labels. 0.50pp sits comfortably inside both ends.
# Open question Q2 for Zaeem: two labelled rows is thin evidence for a global constant, and a
# per-key threshold in facts/catalog.yaml alongside max_age_days would be better founded.
MARKET_RATE_MATERIAL_PP = 0.50


@dataclass(frozen=True)
class Verdict:
    """One decision about one claim, and the rule that made it.

    `rule_fired` is not optional and is never blank. A verdict nobody can trace to a rule is a bug,
    not a default — it is the difference between a product that can be audited and one that cannot.
    """

    claim: Claim
    status: Status
    rule_fired: str
    detail: str
    fact: Fact | None = None
    expected: float | None = None
    delta: float | None = None

    @property
    def is_stale(self) -> bool:
        return self.status in {"stale_material", "stale_immaterial"}


def judge(claim: Claim) -> Verdict:
    """Return exactly one Verdict for a Claim. Total: every path ends in a rule."""
    for rule in (_r1_historical_window, _r2_unjudged_claim_type, _r3_no_resolvable_fact,
                 _r4_matches_current, _r5_diverges_from_current):
        verdict = rule(claim)
        if verdict is not None:
            return verdict
    raise AssertionError(f"no rule fired for {claim.source_id}:{claim.locator} — judge is not total")


def judge_all(claims: list[Claim]) -> list[Verdict]:
    return [judge(claim) for claim in claims]


# ------------------------------------------------------------------------------------- the rules


def _r1_historical_window(claim: Claim) -> Verdict | None:
    """A claim the speaker placed in the past is resolved against history, never against today."""
    if claim.claim_type != "historical":
        return None
    if claim.entity_key is None or claim.year_hint is None or claim.value is None:
        return None  # nothing to resolve against; R2 will skip it

    windows = windows_for(claim.entity_key, claim.year_hint)
    if not windows:
        # The F-2a trap. No covering window is MISSING EVIDENCE, not a contradiction. Reading it
        # as "the creator was wrong" puts a confident false verdict on an accurate sentence.
        return Verdict(
            claim=claim, status="unresolved", rule_fired="R1.historical_window_missing",
            detail=(f"{claim.entity_key} has no catalog window covering {claim.year_hint}; "
                    "cannot confirm or dispute a past value"),
        )

    values = [w.value for w in windows if w.value is not None]
    if any(_equal(claim.value, v) for v in values):
        matched = next(w for w in windows if w.value is not None and _equal(claim.value, w.value))
        return Verdict(
            claim=claim, status="skip", rule_fired="R1.historical_matches_window",
            detail=(f"{claim.value:g} matches {claim.entity_key} for "
                    f"{matched.effective_from}..{matched.effective_to}; accurate as stated"),
            expected=matched.value,
        )

    # A past value the catalog contradicts. Almanac does not have a verdict for "the creator
    # misremembered", and inventing one here would assert more than the rules can ground, so this
    # surfaces for human eyes instead. Open question Q5 for Zaeem.
    return Verdict(
        claim=claim, status="unresolved", rule_fired="R1.historical_disputed",
        detail=(f"claimed {claim.value:g} for {claim.year_hint}, but {claim.entity_key} windows "
                f"covering that year hold {', '.join(f'{v:g}' for v in values)}"),
        expected=values[0] if values else None,
    )


def _r2_unjudged_claim_type(claim: Claim) -> Verdict | None:
    """illustrative, structural, other, and unkeyed historical are not checkable against facts."""
    if claim.claim_type in JUDGED_CLAIM_TYPES:
        return None
    return Verdict(
        claim=claim, status="skip", rule_fired="R2.unjudged_claim_type",
        detail=f"claim_type={claim.claim_type} is not checked against the facts table",
    )


def _r3_no_resolvable_fact(claim: Claim) -> Verdict | None:
    """A judged claim Almanac's catalog cannot resolve. Common and normal, not a failure."""
    if claim.entity_key is None:
        return Verdict(
            claim=claim, status="unresolved", rule_fired="R3.no_entity_key",
            detail="no catalog key covers this number",
        )
    if claim.value is None:
        return Verdict(
            claim=claim, status="unresolved", rule_fired="R3.no_claimed_value",
            detail=f"{claim.entity_key} resolved, but the claim carries no number to compare",
        )
    fact = current(claim.entity_key)
    if fact.value is None:
        return Verdict(
            claim=claim, status="unresolved", rule_fired="R3.no_fact_value",
            detail=f"{claim.entity_key} has no value in the facts table", fact=fact,
        )
    return None


def _r4_matches_current(claim: Claim) -> Verdict | None:
    """Exact. The labelled set carries no tolerance band: all nine `correct` rows differ by 0.00."""
    fact = current(claim.entity_key)
    if not _equal(claim.value, fact.value):
        return None
    return Verdict(
        claim=claim, status="correct", rule_fired="R4.matches_current",
        detail=f"{claim.value:g} matches {fact.key}" + (" (stale feed)" if fact.stale else ""),
        fact=fact, expected=fact.value, delta=0.0,
    )


def _r5_diverges_from_current(claim: Claim) -> Verdict | None:
    """The only rule that can call something stale. Materiality is per-kind — see the module docstring."""
    fact = current(claim.entity_key)
    kind = load_catalog()[claim.entity_key].kind
    delta = claim.value - fact.value

    if kind in EXACT_MATCH_KINDS:
        status: Status = "stale_material"
        why = f"{kind} is an exact-match domain: any difference is material"
    elif abs(delta) >= MARKET_RATE_MATERIAL_PP:
        status = "stale_material"
        why = f"moved {abs(delta):.2f}pp, at or over the {MARKET_RATE_MATERIAL_PP:.2f}pp bar"
    else:
        status = "stale_immaterial"
        why = f"moved {abs(delta):.2f}pp, under the {MARKET_RATE_MATERIAL_PP:.2f}pp bar"

    return Verdict(
        claim=claim, status=status, rule_fired=f"R5.{status}",
        detail=(f"video says {claim.value:g}, {fact.key} is {fact.value:g} "
                f"({delta:+g}); {why}" + (" [stale feed]" if fact.stale else "")),
        fact=fact, expected=fact.value, delta=delta,
    )


def _equal(a: float | None, b: float | None, tolerance: float = 1e-9) -> bool:
    """Float equality for money and rates. A tolerance this tight is equality, not a band."""
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerance
