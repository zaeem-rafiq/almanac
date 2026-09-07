"""Offline tests for the eval harness. No model, no network.

The harness's job is to be believed, so these tests attack the ways a scorer lies:

  * a gate that passes because the pipeline produced nothing
  * recall computed over the rows extraction happened to find
  * one verdict satisfying several labels
  * a match threshold loose enough to pair unrelated sentences
  * a run that silently writes to the labelled set it is scoring against
"""

from __future__ import annotations

import hashlib
import json
import typing

import pytest

from almanac import evaluate
from almanac.evaluate import (
    HARD_NEGATIVE_CLAIM_TYPES,
    coverage,
    MIN_LABEL_COVERAGE,
    STALE_MATERIAL_RECALL_FLOOR,
    EvalResult,
    Label,
    gate_failures,
    iou,
    load_labels,
    match,
    normalised_span,
    render_markdown,
    run,
    to_payload,
    write_results,
)
from almanac.extract import Source
from almanac.judge import Verdict
from almanac.models import Claim, ClaimType

SRC = "corpus/channel/v1-401k-limits-explained/captions.srt"


def source(text: str, source_id: str = SRC) -> Source:
    return Source(source_id, text, [])


def verdict(quote: str, status: str, *, claim_type: str = "statutory_limit",
            rule: str = "R5.stale_material", source_id: str = SRC) -> Verdict:
    claim = Claim(
        source_id=source_id, locator="00:00:01,000", quote=quote,
        claim_type=claim_type, entity_key=None, value=1.0, unit="usd", confidence=0.9,
    )
    return Verdict(claim=claim, status=status, rule_fired=rule, detail="fixture")


def label(quote: str, status: str, *, claim_type: str = "statutory_limit",
          line: int = 1, source_id: str = SRC) -> Label:
    return Label(line=line, source=source_id, quote=quote, expected_claim_type=claim_type,
                 expected_entity_key=None, expected_status=status)


def pair(label: Label, verdict: Verdict, coverage: float = 1.0,
         overlap: float | None = None) -> evaluate.Match:
    """Build a Match without spelling out its arity in every fixture."""
    return evaluate.Match(label, verdict, coverage, coverage if overlap is None else overlap)


def result(**overrides) -> EvalResult:
    payload = dict(
        labels=[], matches=[], unmatched_labels=[], unlocatable_labels=[],
        unmatched_verdicts=[], sources_read=[SRC], verdicts_produced=1, min_coverage=MIN_LABEL_COVERAGE,
    )
    payload.update(overrides)
    return EvalResult(**payload)


# --------------------------------------------------------------------------- the labelled set


def test_the_real_labelled_set_loads_with_the_shape_the_metrics_assume():
    """54 rows, and the field is `expected_claim_type` — there is no `claim_type` key."""
    labels = load_labels()
    assert len(labels) == 54
    assert {l.expected_status for l in labels} <= set(evaluate.STATUSES)
    assert {l.expected_claim_type for l in labels} <= set(typing.get_args(ClaimType))
    assert labels[0].line == 1


def test_structural_is_a_first_class_claim_type_not_an_unknown():
    """A-03 added `structural` as the seventh label; four rows use it, all `skip`."""
    labels = load_labels()
    structural = [l for l in labels if l.expected_claim_type == "structural"]
    assert len(structural) == 4
    assert {l.expected_status for l in structural} == {"skip"}
    assert "structural" in evaluate.CLAIM_TYPES

    res = result(labels=structural, matches=[
        pair(l, verdict(l.quote, "skip", claim_type="structural",
                                  rule="R2.unjudged_claim_type"), 1.0)
        for l in structural
    ])
    assert res.claim_type_accuracy == 1.0
    assert ("structural", "structural") in res.claim_type_confusion()
    assert "structural" in res.claim_types_seen()


def test_the_hard_negative_set_is_not_empty():
    """A gate over an empty set is a gate over nothing. 27 illustrative + 1 historical."""
    labels = load_labels()
    hard = [l for l in labels if l.is_hard_negative]
    assert len(hard) == 28
    assert {l.expected_claim_type for l in hard} == HARD_NEGATIVE_CLAIM_TYPES
    assert {l.expected_status for l in hard} == {"skip"}


# ------------------------------------------------------------------------------- the matching


def test_coverage_is_containment_of_the_label_not_span_identity():
    """The metric that distinguishes 'extraction missed this' from 'extraction quoted more'."""
    label_span = (10, 20)
    assert coverage(label_span, (0, 100)) == 1.0, "a longer quote containing the label covers it"
    assert coverage(label_span, (10, 20)) == 1.0
    assert coverage(label_span, (15, 20)) == pytest.approx(0.5)
    assert coverage(label_span, (30, 40)) == 0.0
    # ...and this is exactly where IoU disagrees, which is why the harness does not use it.
    assert iou(label_span, (0, 100)) == pytest.approx(0.1)


def test_a_verdict_quoting_the_whole_sentence_matches_a_fragment_label():
    """The real shape of the labelled set: labels are fragments, extracted quotes are sentences."""
    text = "Say you make seventy thousand dollars and your plan matches six percent of your salary."
    src = {SRC: source(text)}
    labels = [label("Say you make seventy thousand dollars", "skip", claim_type="illustrative")]
    verdicts = {SRC: [verdict(text, "skip", claim_type="illustrative",
                              rule="R2.unjudged_claim_type")]}
    matches, unmatched, _, _ = match(labels, verdicts, src)
    assert len(matches) == 1 and unmatched == []
    assert matches[0].coverage == pytest.approx(1.0)
    assert matches[0].overlap < 0.5, "IoU would have called this a miss"


def test_a_verdict_covering_only_part_of_the_label_is_not_a_match():
    """Coverage is a floor, not a licence: partial containment still fails."""
    text = "an extra five hundred dollars a month you could throw at either side"
    src = {SRC: source(text)}
    labels = [label(text, "skip", claim_type="illustrative")]
    verdicts = {SRC: [verdict("an extra five hundred", "skip", claim_type="illustrative")]}
    matches, unmatched, _, _ = match(labels, verdicts, src)
    assert matches == [] and len(unmatched) == 1


def test_the_tighter_of_two_covering_verdicts_wins_the_pair():
    text = "the catch up is seven thousand five hundred dollars for savers over fifty this year"
    src = {SRC: source(text)}
    labels = [label("the catch up is seven thousand five hundred dollars", "stale_material")]
    verdicts = {SRC: [
        verdict(text, "skip"),
        verdict("the catch up is seven thousand five hundred dollars", "stale_material"),
    ]}
    matches, _, _, spare = match(labels, verdicts, src)
    assert len(matches) == 1
    assert matches[0].coverage == pytest.approx(1.0)
    assert matches[0].overlap == pytest.approx(1.0), "the looser quote won the tie"
    assert matches[0].verdict.status == "stale_material"
    assert len(spare) == 1


def test_iou_is_zero_for_disjoint_spans_and_one_for_identical_ones():
    assert iou((0, 10), (20, 30)) == 0.0
    assert iou((0, 10), (10, 20)) == 0.0  # touching is not overlapping
    assert iou((0, 10), (0, 10)) == 1.0
    assert iou((0, 10), (0, 20)) == pytest.approx(0.5)


def test_a_quote_is_located_under_whitespace_normalisation_only():
    text = "the limit is\n  twenty three   thousand dollars this year"
    assert normalised_span(text, "the limit  is twenty three thousand") is not None
    assert normalised_span(text, "the limit is twenty four thousand") is None


def test_one_verdict_cannot_satisfy_two_labels():
    """Many-to-one assignment would let a single claim inflate recall to whatever is needed."""
    text = "you can defer twenty three thousand dollars into your 401k this year"
    src = {SRC: source(text)}
    labels = [
        label("you can defer twenty three thousand dollars", "stale_material", line=1),
        label("twenty three thousand dollars into your 401k", "stale_material", line=2),
    ]
    verdicts = {SRC: [verdict("you can defer twenty three thousand dollars into your 401k", "stale_material")]}

    matches, unmatched, unlocatable, spare = match(labels, verdicts, src)
    assert len(matches) == 1, "a second label was paired with an already-spent verdict"
    assert len(unmatched) == 1
    assert unlocatable == [] and spare == []
    assert {m.label.line for m in matches} | {l.line for l in unmatched} == {1, 2}


def test_a_non_covering_verdict_is_never_preferred():
    text = "the catch up is seven thousand five hundred dollars for savers over fifty"
    src = {SRC: source(text)}
    labels = [label("the catch up is seven thousand five hundred dollars", "stale_material")]
    verdicts = {SRC: [
        verdict("for savers over fifty", "skip"),
        verdict("the catch up is seven thousand five hundred dollars", "stale_material"),
    ]}
    matches, _, _, spare = match(labels, verdicts, src)
    assert len(matches) == 1
    assert matches[0].verdict.status == "stale_material"
    assert matches[0].coverage == pytest.approx(1.0)
    assert len(spare) == 1


def test_an_unrelated_sentence_is_not_matched():
    text = "the limit is twenty three thousand dollars. the catch up is seven thousand five hundred."
    src = {SRC: source(text)}
    labels = [label("the limit is twenty three thousand dollars", "stale_material")]
    verdicts = {SRC: [verdict("the catch up is seven thousand five hundred", "correct")]}
    matches, unmatched, _, _ = match(labels, verdicts, src)
    assert matches == []
    assert len(unmatched) == 1


def test_a_label_absent_from_its_own_source_is_its_own_category_not_a_miss():
    """Label/corpus drift is a data fault. Filing it as a pipeline miss would blame the wrong thing."""
    src = {SRC: source("the limit is twenty three thousand dollars")}
    labels = [label("a sentence nobody ever said", "skip")]
    matches, unmatched, unlocatable, _ = match(labels, {SRC: []}, src)
    assert matches == [] and unmatched == []
    assert len(unlocatable) == 1
    assert gate_failures(result(labels=labels, unlocatable_labels=unlocatable,
                                matches=[], verdicts_produced=3,
                                unmatched_verdicts=[verdict("x", "skip")]))


def test_matching_never_crosses_source_boundaries():
    other = "corpus/scripts/fresh_ok.md"
    text = "the limit is twenty three thousand dollars"
    src = {SRC: source(text), other: source(text, other)}
    labels = [label("the limit is twenty three thousand dollars", "stale_material", source_id=SRC)]
    verdicts = {other: [verdict("the limit is twenty three thousand dollars", "correct",
                                source_id=other)]}
    matches, unmatched, _, _ = match(labels, verdicts, src)
    assert matches == [], "a verdict from a different video was paired with this label"
    assert len(unmatched) == 1


# -------------------------------------------------------------------------------- the metrics


def test_recall_counts_rows_extraction_never_found():
    """Otherwise the extractor could raise recall by finding fewer claims."""
    found = label("found", "stale_material", line=1)
    missed = label("missed", "stale_material", line=2)
    res = result(
        labels=[found, missed],
        matches=[pair(found, verdict("found", "stale_material"), 1.0)],
        unmatched_labels=[missed],
    )
    score = res.status_score("stale_material")
    assert score.support == 2, "support dropped the row extraction missed"
    assert score.true_positives == 1
    assert score.false_negatives == 1
    assert score.not_extracted == 1
    assert score.recall == pytest.approx(0.5)
    assert res.extraction_recall == pytest.approx(0.5)


def test_precision_and_recall_arithmetic_is_the_textbook_one():
    labels = [
        label("a", "stale_material", line=1),
        label("b", "stale_material", line=2),
        label("c", "skip", line=3),
    ]
    matches = [
        pair(labels[0], verdict("a", "stale_material"), 1.0),   # TP
        pair(labels[1], verdict("b", "skip"), 1.0),             # FN for stale_material
        pair(labels[2], verdict("c", "stale_material"), 1.0),   # FP for stale_material
    ]
    res = result(labels=labels, matches=matches, verdicts_produced=3)
    score = res.status_score("stale_material")
    assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 1, 1)
    assert score.precision == pytest.approx(0.5)
    assert score.recall == pytest.approx(0.5)

    skip = res.status_score("skip")
    assert (skip.true_positives, skip.false_positives, skip.false_negatives) == (0, 1, 1)


def test_a_status_with_no_labelled_row_reports_none_not_zero():
    """0.00 reads as 'we scored it and it failed'. None reads as 'nothing to score'."""
    res = result(labels=[label("a", "skip")],
                 matches=[pair(label("a", "skip"), verdict("a", "skip"), 1.0)])
    assert res.status_score("correct").recall is None, "a status with no labelled row"
    assert res.status_score("correct").precision is None, "a status nothing predicted"


def test_claim_type_accuracy_is_scored_over_matched_rows():
    labels = [label("a", "skip", claim_type="illustrative", line=1),
              label("b", "skip", claim_type="structural", line=2)]
    matches = [
        pair(labels[0], verdict("a", "skip", claim_type="illustrative"), 1.0),
        pair(labels[1], verdict("b", "skip", claim_type="other"), 1.0),
    ]
    res = result(labels=labels, matches=matches, verdicts_produced=2)
    assert res.claim_type_accuracy == pytest.approx(0.5)
    assert res.claim_type_confusion()[("structural", "other")] == 1
    assert len(res.claim_type_misses()) == 1


# ----------------------------------------------------------------------------------- the gate


def test_gate_passes_only_on_a_run_that_was_actually_alive():
    labels = [label("a", "stale_material", line=1), label("b", "skip",
                                                          claim_type="illustrative", line=2)]
    matches = [
        pair(labels[0], verdict("a", "stale_material"), 1.0),
        pair(labels[1], verdict("b", "skip", claim_type="illustrative",
                                          rule="R2.unjudged_claim_type"), 1.0),
    ]
    res = result(labels=labels, matches=matches, verdicts_produced=2)
    assert res.verdicts_produced > 0 and res.matched > 0, "the passing fixture must be non-empty"
    assert gate_failures(res) == []


def test_gate_fails_a_run_that_produced_nothing_instead_of_passing_vacuously():
    """The adversarial probe: zero false positives is exactly what an empty run reports."""
    labels = load_labels()
    empty = result(labels=labels, verdicts_produced=0, sources_read=[SRC])
    failures = gate_failures(empty)
    assert failures, "an empty run passed the hard-negative gate"
    assert "0 verdicts produced" in failures[0]
    assert empty.hard_negative_false_positives == [], "the trivially-passing negative is still zero"


def test_gate_fails_when_verdicts_exist_but_nothing_matched():
    labels = load_labels()
    res = result(labels=labels, verdicts_produced=40, matches=[],
                 unmatched_verdicts=[verdict("x", "skip")] * 40)
    failures = gate_failures(res)
    assert any("none could be paired" in f for f in failures)


def test_gate_fails_with_no_sources():
    assert gate_failures(result(sources_read=[], verdicts_produced=0))


def test_gate_fails_on_a_single_hard_negative_false_positive():
    """One illustrative sentence called stale is a confident false correction — ship-blocking."""
    good = label("a", "stale_material", line=1)
    trap = label("say you put in ten thousand dollars", "skip",
                 claim_type="illustrative", line=2)
    matches = [
        pair(good, verdict("a", "stale_material"), 1.0),
        pair(trap, verdict("say you put in ten thousand dollars", "stale_material",
                                     claim_type="illustrative"), 1.0),
    ]
    res = result(labels=[good, trap], matches=matches, verdicts_produced=2)
    assert len(res.hard_negative_false_positives) == 1
    assert any("hard-negative false positive" in f for f in gate_failures(res))


def test_a_historical_row_called_stale_is_also_a_hard_negative():
    trap = label("back in 2022 it paid nine point six two percent", "skip",
                 claim_type="historical")
    match_ = pair(trap, verdict(trap.quote, "stale_immaterial", claim_type="historical"), 1.0)
    res = result(labels=[trap], matches=[match_], verdicts_produced=1)
    assert len(res.hard_negative_false_positives) == 1
    assert "SYSTEM" in evaluate._read_of(match_)


def test_gate_fails_when_stale_material_recall_is_under_the_floor():
    labels = [label(f"q{i}", "stale_material", line=i) for i in range(10)]
    matches = [
        pair(l, verdict(l.quote, "stale_material" if i < 8 else "skip"), 1.0)
        for i, l in enumerate(labels)
    ]
    res = result(labels=labels, matches=matches, verdicts_produced=10)
    assert res.status_score("stale_material").recall == pytest.approx(0.8)
    assert any("stale_material recall" in f for f in gate_failures(res))

    at_floor = result(
        labels=labels, verdicts_produced=10,
        matches=[pair(l, verdict(l.quote, "stale_material" if i < 9 else "skip"), 1.0)
                 for i, l in enumerate(labels)],
    )
    assert at_floor.status_score("stale_material").recall == pytest.approx(STALE_MATERIAL_RECALL_FLOOR)
    assert gate_failures(at_floor) == [], "the floor is inclusive: >= 0.90 must pass"


def test_the_two_gate_conditions_are_independent():
    """A run can fail recall while its hard negatives are clean, and the report must say so."""
    labels = [label("a", "stale_material", line=1),
              label("b", "skip", claim_type="illustrative", line=2)]
    matches = [
        pair(labels[0], verdict("a", "skip"), 1.0),
        pair(labels[1], verdict("b", "skip", claim_type="illustrative"), 1.0),
    ]
    res = result(labels=labels, matches=matches, verdicts_produced=2)
    failures = gate_failures(res)
    assert len(res.hard_negative_false_positives) == 0
    assert len(failures) == 1 and "stale_material recall" in failures[0]


# ------------------------------------------------------------------- run(), end to end, offline


@pytest.fixture
def fake_extraction(monkeypatch):
    """Replace the model call with hand-built claims over the REAL corpus and REAL labels.

    Returns a dict the test fills in with a per-label claim_type, plus a `called` flag. The flag
    is asserted in every test that uses this: a stub that silently fails to install would let the
    live extractor run and make these tests pass for the wrong reason.
    """
    state = {"called": False, "claim_type": "illustrative", "quotes": None}

    def fake(sources, *args, **kwargs):
        state["called"] = True
        labels = load_labels()
        by_source: dict[str, list[Claim]] = {s.source_id: [] for s in sources}
        for src in sources:
            for lab in labels:
                if lab.source != src.source_id:
                    continue
                if state["quotes"] is not None and lab.quote not in state["quotes"]:
                    continue
                span = normalised_span(src.text, lab.quote)
                assert span is not None, f"fixture quote not in its source: {lab.quote[:50]}"
                by_source[src.source_id].append(Claim(
                    source_id=src.source_id, locator="00:00:01,000", quote=lab.quote,
                    claim_type=state["claim_type"], entity_key=None, value=None,
                    unit=None, confidence=0.9,
                ))
        return by_source

    monkeypatch.setattr(evaluate, "extract_sources", fake)
    return state


def test_run_scores_the_real_labelled_set_without_touching_a_model(fake_extraction):
    res = run()
    assert fake_extraction["called"], "the stub was not installed — this run hit the real extractor"

    assert len(res.labels) == 54
    assert len(res.sources_read) == 7
    assert res.verdicts_produced == 54
    assert res.matched == 54, "every labelled quote should pair with its own claim"
    assert res.unlocatable_labels == [], "a labelled quote is missing from its source file"
    assert res.extraction_recall == pytest.approx(1.0)

    # Every claim is `illustrative`, so R2 skips all 54. That is a known-by-hand outcome.
    assert {m.verdict.status for m in res.matches} == {"skip"}
    assert {m.verdict.rule_fired for m in res.matches} == {"R2.unjudged_claim_type"}
    assert res.status_score("skip").recall == pytest.approx(1.0)
    assert res.status_score("stale_material").recall == pytest.approx(0.0)
    assert res.claim_type_accuracy == pytest.approx(27 / 54)


def test_the_gate_catches_that_all_skip_run(fake_extraction):
    res = run()
    assert fake_extraction["called"]
    failures = gate_failures(res)
    assert any("stale_material recall" in f for f in failures)
    assert len(res.hard_negative_false_positives) == 0, (
        "an all-skip run has clean hard negatives — the recall floor is what catches it"
    )


def test_the_gate_catches_a_run_that_calls_every_sentence_stale(fake_extraction):
    """The opposite failure: perfect stale_material recall, 28 false corrections."""
    fake_extraction["claim_type"] = "market_rate"
    monkey = evaluate.judge_all
    try:
        evaluate.judge_all = lambda claims: [
            Verdict(claim=c, status="stale_material", rule_fired="R5.stale_material", detail="x")
            for c in claims
        ]
        res = run()
        assert fake_extraction["called"]
        assert res.status_score("stale_material").recall == pytest.approx(1.0)
        assert len(res.hard_negative_false_positives) == 28
        failures = gate_failures(res)
        assert any("hard-negative false positive" in f for f in failures)
        assert not any("stale_material recall" in f for f in failures)
    finally:
        evaluate.judge_all = monkey


def test_run_never_writes_the_labelled_set(fake_extraction, tmp_path):
    """`evals/claims.jsonl` is Zaeem's. The harness reads it and nothing else."""
    before = hashlib.sha256(evaluate.CLAIMS_PATH.read_bytes()).hexdigest()
    res = run()
    md_path, json_path = write_results(res, tmp_path)
    after = hashlib.sha256(evaluate.CLAIMS_PATH.read_bytes()).hexdigest()
    assert before == after, "the eval run modified evals/claims.jsonl"
    assert md_path.parent == tmp_path and json_path.parent == tmp_path
    assert fake_extraction["called"]


def test_the_cache_makes_a_rerun_score_the_same_claims(fake_extraction, tmp_path):
    cache = tmp_path / "extraction.json"
    first = run(cache_path=cache)
    assert fake_extraction["called"] and not first.cached_extraction
    assert cache.is_file()

    fake_extraction["called"] = False
    second = run(cache_path=cache)
    assert not fake_extraction["called"], "the cached run re-extracted"
    assert second.cached_extraction
    assert second.matched == first.matched
    assert second.verdicts_produced == first.verdicts_produced


# ------------------------------------------------------------------------------- the reporting


def test_the_report_shows_every_section_and_both_denominators(fake_extraction, tmp_path):
    res = run()
    md_path, json_path = write_results(res, tmp_path)
    text = md_path.read_text(encoding="utf-8")

    for heading in ("(a) Extraction recall", "(b) claim_type accuracy",
                    "(c) Per-status precision / recall", "(d) Hard negatives", "(e) Misses"):
        assert heading in text, f"the report is missing {heading}"
    for status in evaluate.STATUSES:
        assert f"`{status}`" in text
    assert f"**{res.matched} / {len(res.labels)}**" in text, "the matched COUNT is not printed"
    assert "GATE: FAIL" in text

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["run"]["matched"] == res.matched
    assert payload["run"]["labelled_rows"] == 54
    assert payload["gate"]["passed"] is False
    assert len(payload["status_scores"]) == len(evaluate.STATUSES)
    assert payload["hard_negatives"]["labelled"] == 28


def test_the_report_records_whether_the_match_threshold_is_load_bearing(fake_extraction):
    res = run()
    assert res.sensitivity, "the threshold sweep did not run"
    text = render_markdown(res)
    assert "Coverage-floor sensitivity" in text
    band = {k: v for k, v in res.sensitivity.items() if k >= evaluate.SENSITIVITY_BAND_FLOOR}
    moved_in_band = len(set(band.values())) > 1
    assert ("load-bearing and the choice belongs in the ADR" in text) is moved_in_band
    assert ("Constant at" in text) is not moved_in_band


def test_a_sweep_that_can_never_move_is_reported_as_uninformative():
    """A constant sweep only means something if the sweep is capable of moving."""
    labels = [label("a", "stale_material")]
    res = result(labels=labels, matches=[pair(labels[0], verdict("a", "stale_material"))],
                 sensitivity={0.50: 1, 0.70: 1, 0.90: 1, 1.00: 1})
    assert "cannot detect a load-bearing threshold" in render_markdown(res)

    can_move = result(labels=labels, matches=[pair(labels[0], verdict("a", "stale_material"))],
                      sensitivity={0.50: 2, 0.70: 1, 0.90: 1, 1.00: 1})
    text = render_markdown(can_move)
    assert "Constant at 1 across the operating band" in text
    assert "which shows the sweep can move" in text


def test_a_clean_run_says_so_rather_than_printing_an_empty_table():
    labels = [label("a", "stale_material")]
    res = result(labels=labels,
                 matches=[pair(labels[0], verdict("a", "stale_material"), 1.0)])
    text = render_markdown(res)
    assert "GATE: PASS" in text
    assert "None. Every labelled row matched a verdict" in text
    assert to_payload(res)["gate"]["passed"] is True
