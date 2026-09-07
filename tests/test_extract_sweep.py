"""The coverage sweep in extract.py (ADR-002 D-5).

Extraction is a model call and does not repeat itself; the sampling knobs that would pin it are
deprecated on this model (ADR-000 §10). The sweep is the code-side answer: mark every sentence
that certainly carries a number, and re-read any the first pass produced no claim for.

These tests cover the pure functions. That the sweep actually recovers the claim that used to drop
is a live measurement, recorded in docs/proofs/ — not something a unit test can assert.
"""

from almanac.extract import (
    carries_a_number,
    number_bearing_spans,
    uncovered_sentences,
)
from almanac.models import Claim


def _claim(quote: str) -> Claim:
    return Claim(
        source_id="t", locator="00:00:01,000", quote=quote,
        claim_type="market_rate", entity_key=None, value=None, unit=None,
        year_hint=None, confidence=0.9,
    )


# --------------------------------------------------------------- the detector fires, and doesn't

def test_detects_digits():
    assert carries_a_number("The limit is $23,000 this year.")


def test_detects_numbers_spelled_out():
    """The corpus spells numbers out — a digit-only regex would miss the sentence that drops."""
    assert carries_a_number("The composite rate right now is three point one one percent.")
    assert carries_a_number("You can defer twenty three thousand dollars.")


def test_does_not_fire_on_ordinary_prose():
    """The control: if this returned True the sweep would re-read everything and prove nothing."""
    assert not carries_a_number("That is the whole idea behind it.")
    assert not carries_a_number("Let me explain why this matters to you.")


def test_a_bare_number_word_alone_is_not_enough():
    """'one' is common English. Requiring a run or a unit is what keeps the sweep cheap."""
    assert not carries_a_number("This is one of the things people get wrong.")


# ------------------------------------------------------------------------------ span mechanics

def test_spans_cover_only_the_number_bearing_sentences():
    text = "Nothing here. The rate is four percent. Still nothing."
    spans = number_bearing_spans(text)
    assert len(spans) == 1
    assert "four percent" in text[spans[0][0]:spans[0][1]]


def test_uncovered_is_empty_when_a_claim_overlaps():
    text = "The composite rate right now is three point one one percent."
    assert uncovered_sentences(text, [_claim(text.strip())]) == []


def test_uncovered_reports_the_sentence_no_claim_reached():
    text = "The rate is four percent. The limit is seven thousand dollars."
    missing = uncovered_sentences(text, [_claim("The rate is four percent.")])
    assert len(missing) == 1
    assert "seven thousand dollars" in missing[0]


def test_uncovered_returns_every_number_sentence_when_extraction_produced_nothing():
    """The run-2/run-3 case: the first pass returns almost nothing and the sweep is the floor."""
    text = "The rate is four percent. Nothing numeric. The limit is seven thousand dollars."
    assert len(uncovered_sentences(text, [])) == 2
