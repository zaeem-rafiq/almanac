"""Offline tests for judge.py. No model, no network.

Every rule is tested on the branch where it fires AND on the branch where it defers, because a
rule that fires unconditionally scores identically to a correct one on a happy-path suite.
"""

from __future__ import annotations

import typing

import pytest

from almanac import judge as judge_mod
from almanac.catalog import windows_for
from almanac.judge import (
    EXACT_MATCH_KINDS,
    JUDGED_CLAIM_TYPES,
    MARKET_RATE_MATERIAL_PP,
    Status,
    Verdict,
    judge,
    judge_all,
)
from almanac.models import Claim, ClaimType


def claim(**overrides) -> Claim:
    payload = {
        "source_id": "corpus/channel/v1-401k-limits-explained/captions.srt",
        "locator": "00:00:12,000",
        "quote": "a quote",
        "claim_type": "statutory_limit",
        "entity_key": "k401_employee_deferral",
        "value": 24_500.0,
        "unit": "usd",
        "year_hint": None,
        "confidence": 0.9,
    }
    payload.update(overrides)
    return Claim(**payload)


# ------------------------------------------------------------------ the module-level contract


def test_judge_never_imports_a_model_client():
    """The inviolable principle: verdicts come from rules, not from a prompt."""
    source = (judge_mod.__file__)
    text = open(source, encoding="utf-8").read()
    for forbidden in ("import anthropic", "from anthropic", "messages.create", "tool_use"):
        assert forbidden not in text, f"judge.py reaches for a model: {forbidden!r}"


def test_every_verdict_carries_a_rule_fired():
    verdicts = judge_all([
        claim(claim_type="illustrative", entity_key=None, value=70_000.0),
        claim(claim_type="statutory_limit", entity_key=None, value=69_000.0),
        claim(value=24_500.0),
        claim(value=23_000.0),
        claim(claim_type="market_rate", entity_key="mortgage_30y_fixed", value=6.85, unit="pct"),
    ])
    assert len(verdicts) == 5
    for verdict in verdicts:
        assert verdict.rule_fired, f"{verdict.status} verdict with no rule_fired"
        assert verdict.detail


def test_judge_is_total_over_every_claim_type():
    """A claim_type with no rule would raise. Test the whole vocabulary, not the common cases."""
    for claim_type in typing.get_args(ClaimType):
        verdict = judge(claim(claim_type=claim_type, entity_key=None, value=1.0))
        assert isinstance(verdict, Verdict)
        assert verdict.rule_fired
        assert verdict.status in typing.get_args(Status)


# --------------------------------------------------------------- R1: the historical window


def test_r1_historical_matching_a_past_window_is_skipped_not_called_stale():
    """The F-2a trap, and the sharpest row in the corpus.

    9.62 is 125.8% away from today's I-bond rate. Judged against `current` it is the loudest
    divergence in the corpus; judged against the 2022 window it is simply accurate.
    """
    verdict = judge(claim(
        claim_type="historical", entity_key="ibond_composite_rate",
        value=9.62, unit="pct", year_hint=2022,
    ))
    assert verdict.status == "skip"
    assert verdict.rule_fired == "R1.historical_matches_window"
    assert "2022-05-01" in verdict.detail


def test_r1_missing_window_is_unresolved_never_a_contradiction():
    """Absence of evidence must not become a stale verdict on an accurate sentence."""
    assert windows_for("ibond_composite_rate", 2019) == []
    verdict = judge(claim(
        claim_type="historical", entity_key="ibond_composite_rate",
        value=1.18, unit="pct", year_hint=2019,
    ))
    assert verdict.status == "unresolved"
    assert verdict.rule_fired == "R1.historical_window_missing"
    assert not verdict.is_stale


def test_r1_value_the_catalog_contradicts_is_surfaced_not_asserted_stale():
    verdict = judge(claim(
        claim_type="historical", entity_key="ibond_composite_rate",
        value=15.0, unit="pct", year_hint=2022,
    ))
    assert verdict.status == "unresolved"
    assert verdict.rule_fired == "R1.historical_disputed"
    assert "9.62" in verdict.detail


def test_r1_defers_when_the_historical_claim_has_no_key_or_no_year():
    """R1 must not swallow claims it cannot resolve — R2 skips them."""
    for overrides in ({"entity_key": None}, {"year_hint": None}):
        payload = {"claim_type": "historical", "value": 9.62, "year_hint": 2022, **overrides}
        verdict = judge(claim(**payload))
        assert verdict.rule_fired == "R2.unjudged_claim_type"


def test_a_year_hint_alone_does_not_route_a_live_claim_to_history():
    """The converse of R1, and the reason it is narrow.

    "For 2024 you can defer twenty three thousand dollars" names a past year, but the speaker
    asserts it as the live limit. It must be judged against today and come back stale.
    """
    verdict = judge(claim(value=23_000.0, year_hint=2024))
    assert verdict.status == "stale_material"
    assert verdict.rule_fired.startswith("R5.")


# -------------------------------------------------------------------------- R2 and R3


@pytest.mark.parametrize("claim_type", ["illustrative", "structural", "other"])
def test_r2_skips_the_unjudged_types(claim_type):
    verdict = judge(claim(claim_type=claim_type, entity_key=None, value=3.0))
    assert verdict.status == "skip"
    assert verdict.rule_fired == "R2.unjudged_claim_type"


def test_r2_defers_for_every_judged_type():
    for claim_type in JUDGED_CLAIM_TYPES:
        verdict = judge(claim(claim_type=claim_type, entity_key=None, value=1.0))
        assert verdict.rule_fired != "R2.unjudged_claim_type"


def test_r3_unkeyed_judged_claim_is_unresolved_not_skipped():
    """36 of 54 labelled rows carry no key. Unresolved is normal, and it is not `skip`."""
    verdict = judge(claim(entity_key=None, value=69_000.0))
    assert verdict.status == "unresolved"
    assert verdict.rule_fired == "R3.no_entity_key"


def test_r3_claim_without_a_number_is_unresolved():
    verdict = judge(claim(value=None))
    assert verdict.status == "unresolved"
    assert verdict.rule_fired == "R3.no_claimed_value"


# ------------------------------------------------------------------------- R4 and R5


def test_r4_is_exact_not_a_tolerance_band():
    """All nine labelled `correct` rows differ by exactly 0.00 — there is no band to widen."""
    assert judge(claim(value=24_500.0)).status == "correct"
    near_miss = judge(claim(value=24_499.0))
    assert near_miss.status == "stale_material"
    assert near_miss.rule_fired.startswith("R5.")


def test_r5_statutory_limit_is_material_at_any_size():
    """The measured finding: a statutory limit is an exact-match domain."""
    tiny = judge(claim(value=24_499.99))
    assert tiny.status == "stale_material"
    assert "exact-match domain" in tiny.detail


def test_r5_market_rate_materiality_is_a_measured_threshold():
    small = judge(claim(claim_type="market_rate", entity_key="mortgage_30y_fixed",
                        value=6.85, unit="pct"))
    assert small.status == "stale_immaterial", small.detail

    big = judge(claim(claim_type="market_rate", entity_key="ibond_composite_rate",
                      value=3.11, unit="pct"))
    assert big.status == "stale_material", big.detail


def test_r5_threshold_actually_governs_the_outcome(monkeypatch):
    """Adversarial probe: a threshold nothing depends on would score identically.

    If the immaterial row stays immaterial when the bar drops below its own delta, then the bar is
    decorative and the two statuses are being decided by something else.
    """
    baseline = judge(claim(claim_type="market_rate", entity_key="mortgage_30y_fixed",
                           value=6.85, unit="pct"))
    assert baseline.status == "stale_immaterial"
    assert abs(baseline.delta) == pytest.approx(0.14, abs=0.005)

    monkeypatch.setattr(judge_mod, "MARKET_RATE_MATERIAL_PP", 0.10)
    flipped = judge(claim(claim_type="market_rate", entity_key="mortgage_30y_fixed",
                          value=6.85, unit="pct"))
    assert flipped.status == "stale_material", "the threshold does not govern the verdict"


def test_the_measured_threshold_sits_inside_the_observed_gap():
    """0.14pp was labelled immaterial and 1.15pp material; the bar must separate them."""
    assert 0.14 < MARKET_RATE_MATERIAL_PP < 1.15


def test_exact_match_kinds_are_the_two_the_evidence_supports():
    assert EXACT_MATCH_KINDS == {"statutory_limit", "tax_bracket"}
    assert JUDGED_CLAIM_TYPES == {"statutory_limit", "tax_bracket", "market_rate"}


def test_a_stale_feed_annotates_the_verdict_but_does_not_suppress_it():
    """A-01's rule: stale annotates, never suppresses. A stale fact still produces a verdict."""
    verdict = judge(claim(claim_type="market_rate", entity_key="ibond_composite_rate",
                          value=3.11, unit="pct"))
    assert verdict.status == "stale_material"
    assert verdict.fact is not None
