"""Offline tests for notes.py — the code assertion, not the prose.

No test here calls a model. `draft_note` is exercised through a fake client whose replies are
scripted, because what is under test is not what the model says: it is what the CODE does with
what the model says. A prompt that asks for no advice is a request. `check_note()` is the promise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date

import pytest

from almanac.judge import judge
from almanac.models import Claim
from almanac.judge import Verdict
from almanac import notes as notes_module
from almanac.notes import (
    ADVICE_VERBS,
    MAX_NOTE_CHARS,
    annotate,
    check_note,
    draft_note,
    fallback_note,
    format_value,
    required_strings,
)

def stale_usd_verdict() -> Verdict:
    """V1's real case: the video says $23,000, the 2026 limit is $24,500."""
    return judge(
        Claim(
            source_id="corpus/channel/v1-401k-limits-explained/captions.srt",
            locator="00:00:12,000",
            quote="you can defer twenty three thousand dollars of your own money into your 401k",
            claim_type="statutory_limit", entity_key="k401_employee_deferral",
            value=23000.0, unit="usd", confidence=0.95,
        ),
    )


def stale_pct_verdict() -> Verdict:
    """V5's real case: the video says 3.11%, the current composite rate is 4.26%."""
    return judge(
        Claim(
            source_id="corpus/channel/v5-ibonds-vs-high-yield-savings/captions.srt",
            locator="00:00:15,000", quote="The composite rate right now is three point one one percent",
            claim_type="market_rate", entity_key="ibond_composite_rate",
            value=3.11, unit="pct", confidence=0.95,
        ),
    )


# ---------------------------------------------------------------------------- a scripted model

@dataclass
class FakeResponse:
    """What `client.models.generate_content` returns: the reply text on `.text`."""

    text: str


@dataclass
class FakeClient:
    """Replies in order. Records every call so a retry can be proven to have happened.

    Stands in for `google.genai.Client`, whose call is
    `client.models.generate_content(model=, contents=, config=)`. Each call is recorded in a
    NORMALISED shape — `{"model", "system", "messages"}` — so the assertions below read the same
    whichever client the code calls. Only the double moved; nothing it asserts did.
    """

    replies: list
    calls: list = field(default_factory=list)

    def __post_init__(self):
        self.models = self

    def generate_content(self, *, model=None, contents=None, config=None):
        messages = [
            {"role": turn["role"], "content": turn["parts"][0]["text"]}
            for turn in (contents or [])
        ]
        self.calls.append({
            "model": model,
            "system": getattr(config, "system_instruction", None),
            "messages": messages,
            "config": config,
        })
        return FakeResponse(self.replies[min(len(self.calls) - 1, len(self.replies) - 1)])


# ------------------------------------------------------------------------------- format_value

@pytest.mark.parametrize(
    "value, unit, expected",
    [
        (24500.0, "usd", "$24,500"),
        (7500.0, "usd", "$7,500"),
        (184500.0, "usd", "$184,500"),
        (1234.56, "usd", "$1,234.56"),
        (6.71, "pct", "6.71%"),
        (4.26, "pct", "4.26%"),
        (9.0, "pct", "9%"),
        (None, "usd", ""),
    ],
)
def test_format_value_renders_the_string_a_viewer_reads(value, unit, expected):
    assert format_value(value, unit) == expected


def test_the_string_the_prompt_asks_for_is_the_string_the_code_checks():
    """One definition, used to instruct and to judge. Two would make the assertion decorative."""
    verdict = stale_usd_verdict()
    value_string, year = required_strings(verdict)
    assert value_string == "$24,500"
    assert year == "2026"
    assert value_string in fallback_note(verdict)


# --------------------------------------------------------------------------------- check_note

def test_check_note_accepts_a_note_that_states_the_number_and_the_year():
    verdict = stale_usd_verdict()
    note = "📌 Update: the 401(k) deferral limit is $24,500 for 2026. This video quotes $23,000."
    assert check_note(note, verdict) == []


def test_check_note_rejects_a_note_missing_the_current_value():
    verdict = stale_usd_verdict()
    problems = check_note("📌 Update: this number changed for 2026.", verdict)
    assert any("$24,500" in p for p in problems)


def test_check_note_rejects_a_note_missing_the_effective_year():
    verdict = stale_usd_verdict()
    problems = check_note("📌 Update: the limit is now $24,500.", verdict)
    assert any("effective year" in p for p in problems)


@pytest.mark.parametrize("verb", ADVICE_VERBS)
def test_check_note_rejects_every_advice_verb(verb):
    verdict = stale_usd_verdict()
    note = f"📌 Update: the limit is $24,500 for 2026, so you {verb} it."
    problems = check_note(note, verdict)
    assert any("gives advice" in p for p in problems), f"{verb!r} slipped through"


def test_check_note_matches_advice_verbs_on_word_boundaries_only():
    """`must` inside `mustard` is not advice. A checker that fires on substrings cries wolf."""
    verdict = stale_usd_verdict()
    note = "📌 Update: the limit is $24,500 for 2026, up from the $23,000 in this video."
    assert check_note(note, verdict) == []
    assert check_note(note.replace("up from", "buy more than"), verdict)


def test_check_note_rejects_a_note_over_the_character_limit():
    verdict = stale_usd_verdict()
    note = "📌 $24,500 2026 " + "x" * MAX_NOTE_CHARS
    problems = check_note(note, verdict)
    assert any("limit is 280" in p for p in problems)


def test_check_note_rejects_an_empty_note():
    assert check_note("   ", stale_usd_verdict()) == ["note is empty"]


def test_check_note_rejects_a_verdict_with_nothing_to_state():
    """A verdict with no current value cannot be phrased honestly, so no note may claim it was."""
    verdict = replace(stale_usd_verdict(), expected=None, fact=None)
    problems = check_note("📌 Update: something changed.", verdict)
    assert "verdict carries no current value to state" in problems
    assert "verdict carries no effective date" in problems


# ------------------------------------------------------------------------------ fallback_note

@pytest.mark.parametrize("factory", [stale_usd_verdict, stale_pct_verdict])
def test_the_fallback_passes_its_own_assertion(factory):
    verdict = factory()
    assert check_note(fallback_note(verdict), verdict) == []
    assert len(fallback_note(verdict)) <= MAX_NOTE_CHARS


def test_the_fallback_states_both_numbers():
    verdict = stale_pct_verdict()
    note = fallback_note(verdict)
    assert "3.11%" in note and "4.26%" in note and "2026" in note


# --------------------------------------------------------------------------------- draft_note

def test_draft_note_returns_a_passing_model_note_unchanged():
    verdict = stale_usd_verdict()
    good = "📌 Update: the 401(k) deferral limit is $24,500 as of 2026. This video says $23,000."
    client = FakeClient([good])
    note, source = draft_note(verdict, client=client, model="fake")
    assert (note, source) == (good, "model")
    assert len(client.calls) == 1


def test_draft_note_retries_with_the_violations_named_and_accepts_the_second_answer():
    verdict = stale_usd_verdict()
    good = "📌 Update: the limit is $24,500 as of 2026; this video says $23,000."
    client = FakeClient(["📌 You should update this.", good])
    note, source = draft_note(verdict, client=client, model="fake")
    assert (note, source) == (good, "model")
    assert len(client.calls) == 2
    retry = client.calls[1]["messages"][-1]["content"]
    assert "rejected" in retry and "gives advice" in retry


def test_draft_note_falls_back_when_the_model_will_not_comply():
    """Two bad answers must not ship. The code wins, and the artifact records that it had to."""
    verdict = stale_usd_verdict()
    client = FakeClient(["📌 You should buy bonds.", "📌 You must act now."])
    note, source = draft_note(verdict, client=client, model="fake")
    assert source == "fallback"
    assert check_note(note, verdict) == []
    assert len(client.calls) == 2


def test_the_prompt_carries_the_finished_verdict_and_asks_no_question_about_it():
    verdict = stale_usd_verdict()
    client = FakeClient(["📌 The limit is $24,500 as of 2026, not $23,000."])
    draft_note(verdict, client=client, model="fake")
    call = client.calls[0]
    system, user = call["system"], call["messages"][0]["content"]
    assert "$24,500" in system and "2026" in system
    # Whole words only: "correction note" is what the note IS, and the noun must not be confused
    # with the status vocabulary. What the prompt may never do is ask for a verdict.
    forbidden = r"\b(stale|correct|material|materially|immaterial|verdict|decide|judge|check)\b"
    hits = re.findall(forbidden, system, re.I)
    assert not hits, f"the note prompt uses verdict vocabulary: {hits}"
    assert "$24,500" in user and "$23,000" in user


# ------------------------------------------------------------------------------------ annotate

def test_annotate_writes_a_note_for_stale_material_and_for_nothing_else():
    stale = stale_usd_verdict()
    others = [
        replace(stale, status=status, rule_fired=f"{status} fixture")
        for status in ("correct", "stale_immaterial", "skip", "unresolved")
    ]
    client = FakeClient(["📌 Update: the limit is $24,500 as of 2026, not $23,000."])
    notes = annotate([stale, *others], client=client, model="fake")
    key = (stale.claim.source_id, stale.claim.locator)
    assert list(notes) == [key], "a note was drafted for a verdict that had nothing to correct"
    assert notes[key][1] == "model"
    assert len(client.calls) == 1


def test_annotate_never_ships_a_note_that_fails_the_assertion():
    verdict = stale_usd_verdict()
    client = FakeClient(["📌 you should sell", "📌 you must buy"])
    notes = annotate([verdict], client=client, model="fake")
    note, source = notes[(verdict.claim.source_id, verdict.claim.locator)]
    assert source == "fallback"
    assert check_note(note, verdict) == []


def test_a_verdict_carries_no_note_field_at_all():
    """judge.Verdict is judge.py's shape and stays that way — a note is not part of a decision.

    notes.annotate returns prose keyed by (source_id, locator); the report performs the join. That
    is what makes it structurally impossible for this module to alter a status.
    """
    assert not hasattr(stale_usd_verdict(), "note")
    assert "status" not in {f for f in dir(notes_module) if f.startswith("set_")}
