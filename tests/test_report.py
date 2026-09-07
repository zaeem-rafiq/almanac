"""Offline tests for report.py — the presentation layer over judge.py's verdicts.

This file tests the JOIN and the RENDERING. It does not re-test the rules: `tests/test_judge.py`
owns those, and a report that quietly re-decided anything would be the bug this whole design exists
to prevent.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from almanac import models
from almanac.judge import judge, judge_all
from almanac.models import Claim
from almanac.report import (
    Report,
    build_report,
    counts_by_status,
    render_markdown,
    to_row,
    write_report,
)

V1 = "corpus/channel/v1-401k-limits-explained/captions.srt"


def claim(**overrides) -> Claim:
    base = dict(
        source_id=V1, locator="00:00:12,000",
        quote="you can defer twenty three thousand dollars",
        claim_type="statutory_limit", entity_key="k401_employee_deferral",
        value=23000.0, unit="usd", confidence=0.9,
    )
    base.update(overrides)
    return Claim(**base)


# ------------------------------------------------------- models.py stays locked (Zaeem's rule)

def test_the_report_shapes_do_not_live_in_the_locked_models_module():
    """A-03 locked models.py and 609af65 kept it that way. Report rows are not Claim's business."""
    for name in ("Verdict", "Report", "VideoReport", "VerdictRow"):
        assert not hasattr(models, name), f"{name} leaked into the locked models.py"


def test_a_verdict_carries_no_note_field():
    """A note is prose about a decision, not part of one. The join happens in report.to_row."""
    verdict = judge(claim())
    assert not hasattr(verdict, "note")


# ------------------------------------------------------------------------------------- to_row

def test_to_row_flattens_the_verdict_and_keeps_its_rule():
    row = to_row(judge(claim()))
    assert row.status == "stale_material"
    assert row.rule_fired
    assert row.locator == "00:00:12,000"
    assert row.claimed_value == 23000.0
    assert row.current_value == 24500.0
    assert row.entity_key == "k401_employee_deferral"
    assert row.source_url and row.source_url.startswith("https://")


def test_to_row_rounds_the_delta_for_the_artifact():
    """6.85 - 6.71 is 0.13999999999999968 in binary float. A report should not publish that."""
    market = claim(claim_type="market_rate", entity_key="mortgage_30y_fixed",
                   value=6.85, unit="pct")
    row = to_row(judge(market))
    assert row.delta == pytest.approx(0.14)
    assert len(str(abs(row.delta))) < 8, f"unrounded delta leaked into the report: {row.delta}"


def test_to_row_joins_the_note_by_source_and_locator():
    verdict = judge(claim())
    notes = {(V1, "00:00:12,000"): ("📌 Update: it is $24,500 for 2026.", "model")}
    row = to_row(verdict, notes)
    assert row.note == "📌 Update: it is $24,500 for 2026."
    assert row.note_source == "model"


def test_to_row_leaves_the_note_empty_when_none_was_drafted():
    row = to_row(judge(claim()), {})
    assert row.note is None and row.note_source is None


def test_to_row_does_not_misattribute_a_note_from_another_locator():
    """The join key is (source_id, locator). A near-miss must produce no note, not the wrong one."""
    notes = {(V1, "00:99:99,000"): ("📌 wrong row", "model")}
    assert to_row(judge(claim()), notes).note is None


def test_to_row_carries_the_stale_feed_annotation_without_suppressing_the_row():
    """A-01's rule: staleness annotates, never suppresses. cpi_yoy reads stale by design."""
    verdict = judge(claim(claim_type="market_rate", entity_key="cpi_yoy", value=3.36, unit="pct"))
    row = to_row(verdict)
    assert row.current_value is not None
    assert row.status != "unresolved"
    if verdict.fact and verdict.fact.stale:
        assert row.fact_stale is True


def test_to_row_falls_back_to_the_catalog_unit_when_the_claim_has_none():
    row = to_row(judge(claim(unit=None)))
    assert row.unit == "usd"


# ------------------------------------------------------------------------------------- counts

def test_counts_include_the_zeroes():
    counts = counts_by_status([to_row(judge(claim()))])
    assert set(counts) == {"correct", "stale_material", "stale_immaterial", "skip", "unresolved"}
    assert counts["stale_material"] == 1 and counts["skip"] == 0


# ------------------------------------------------------------------------------------- report

def report_fixture() -> Report:
    verdicts = judge_all([
        claim(),
        claim(claim_type="illustrative", value=50000.0, entity_key=None),
        claim(claim_type="market_rate", entity_key="mortgage_30y_fixed", value=6.85, unit="pct"),
    ])
    return build_report("corpus", {V1: verdicts},
                        run_at=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc))


def test_report_counts_match_the_rows():
    report = report_fixture()
    assert sum(report.counts.values()) == 3
    assert report.videos[0].counts == report.counts


def test_report_picks_up_the_video_title_from_meta_json():
    assert "401(k)" in (report_fixture().videos[0].title or "")


def test_markdown_twin_carries_every_rule_fired_unescaped():
    """The rule string in the JSON and in the table must be the same characters.

    A pipe in `rule_fired` would be escaped by the renderer and stop matching the verdict it came
    from, which quietly breaks the audit trail this column exists to provide.
    """
    report = report_fixture()
    markdown = render_markdown(report)
    for row in report.videos[0].verdicts:
        if row.status != "skip":
            assert row.rule_fired in markdown, f"{row.rule_fired!r} did not survive rendering"


def test_report_round_trips_through_json(tmp_path):
    report = report_fixture()
    json_path, md_path = write_report(report, tmp_path)
    restored = Report.model_validate(json.loads(json_path.read_text()))
    assert restored.counts == report.counts
    assert restored.videos[0].verdicts[0].rule_fired == report.videos[0].verdicts[0].rule_fired
    assert md_path.read_text().startswith("# Almanac")
    assert (tmp_path / "2026-09-07.json").is_file()


def test_a_video_with_only_skips_says_so_rather_than_rendering_an_empty_table():
    report = build_report(
        "corpus",
        {"corpus/channel/v4-how-id-invest-10000/captions.srt":
         judge_all([claim(claim_type="illustrative", entity_key=None)])},
    )
    assert "No checkable claims" in render_markdown(report)


def test_the_note_reaches_the_markdown_and_says_when_it_was_a_fallback():
    verdicts = judge_all([claim()])
    notes = {(V1, "00:00:12,000"): ("📌 Update: it is $24,500 for 2026.", "fallback")}
    markdown = render_markdown(build_report("corpus", {V1: verdicts}, notes))
    assert "📌 Update: it is $24,500 for 2026." in markdown
    assert "fallback" in markdown
