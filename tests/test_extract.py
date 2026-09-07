"""Offline tests for the claim extractor.

No network. The model call is the only part not covered here; the live behaviour is proved by
`scripts/proof_a03.py`. Everything the CODE decides — provenance, the verbatim rule, the closed
vocabulary, chunking — is decided here, and so is tested here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from almanac.catalog import load_catalog
from almanac.extract import (
    ExtractedClaim,
    Extraction,
    ExtractionStats,
    Locator,
    Source,
    _admit_entity_key,
    _assemble,
    _trim_to_limit,
    build_system_prompt,
    chunk,
    discover_sources,
    locate,
    locator_for,
    read_script,
    read_srt,
)
from almanac.models import Claim

REPO_ROOT = Path(__file__).resolve().parents[1]
V1 = REPO_ROOT / "corpus/channel/v1-401k-limits-explained/captions.srt"


# ------------------------------------------------------------------------------------ readers


def test_read_srt_joins_cues_with_a_single_space_and_keeps_every_locator():
    source = read_srt(V1)
    raw_cue_count = sum(1 for line in V1.read_text().splitlines() if "-->" in line)

    assert source.source_id == "corpus/channel/v1-401k-limits-explained/captions.srt"
    assert len(source.locators) == raw_cue_count
    assert "  " not in source.text
    for locator in source.locators:
        assert source.text[locator.start : locator.end].strip()


def test_locator_spans_cover_the_document_in_order():
    source = read_srt(V1)
    previous_end = -1
    for locator in source.locators:
        assert locator.start > previous_end
        previous_end = locator.end
    assert source.locators[-1].end == len(source.text)


def test_read_script_labels_lines_as_seen_in_an_editor():
    source = read_script(REPO_ROOT / "corpus/scripts/fresh_ok.md")
    assert source.source_id == "corpus/scripts/fresh_ok.md"
    assert source.locators[0].label == "L1"
    labels = {loc.label for loc in source.locators}
    assert all(label.startswith("L") for label in labels)


def test_discover_sources_finds_the_caption_track_in_a_video_directory():
    sources = discover_sources(REPO_ROOT / "corpus/channel/v1-401k-limits-explained")
    assert [s.source_id for s in sources] == [
        "corpus/channel/v1-401k-limits-explained/captions.srt"
    ]


# ------------------------------------------------------------------- the verbatim rule


def test_every_labelled_quote_in_the_eval_set_is_locatable():
    """The A-02 labels and the A-03 reader must agree on what the document looks like.

    This is the regression that catches a reader change (a different cue join, a stripped
    character) silently making the whole eval set unfindable. It asserts the COUNT checked, so a
    filter bug that skips rows cannot pass as a clean run.
    """
    rows = [
        json.loads(line)
        for line in (REPO_ROOT / "evals/claims.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 54

    cache: dict[str, Source] = {}
    checked = 0
    missing: list[str] = []
    for row in rows:
        source_id = row["source"]
        if source_id not in cache:
            cache[source_id] = discover_sources(REPO_ROOT / source_id)[0]
        span = locate(cache[source_id].text, row["quote"])
        checked += 1
        if span is None:
            missing.append(f"{source_id}: {row['quote'][:60]}")

    assert checked == len(rows), "a row was skipped without being checked"
    assert not missing, f"{len(missing)} labelled quote(s) not found in their source: {missing[:3]}"


def test_locate_returns_the_original_slice_not_the_normalised_one():
    text = "the limit  is\n$23,000 this   year"
    span = locate(text, "the limit is $23,000")
    assert span is not None
    start, end = span
    assert text[start:end] == "the limit  is\n$23,000"


def test_locate_normalises_curly_quotes_in_both_directions():
    assert locate("today’s medical bills", "today's medical bills") is not None
    assert locate("today's medical bills", "today’s medical bills") is not None


def test_locate_refuses_a_paraphrase():
    """The one thing the verbatim proof exists to prevent: a plausible sentence nobody said."""
    source = read_srt(V1)
    assert locate(source.text, "the 401k limit is twenty four thousand five hundred dollars") is None
    assert locate(source.text, "you can defer twenty three thousand dollars into your 401k") is None


def test_locate_rejects_empty_and_whitespace_quotes():
    assert locate("some text", "") is None
    assert locate("some text", "   ") is None


def test_trim_keeps_the_prefix_verbatim():
    text = "word " * 80
    start, end = _trim_to_limit(text, 0, len(text))
    assert end - start <= 200
    assert text[start:end] in text
    assert not text[start:end].endswith(" ")


# ------------------------------------------------------------------ the closed vocabulary


def test_admit_entity_key_accepts_catalog_keys_and_rejects_everything_else():
    allowed = set(load_catalog().keys())
    assert "k401_employee_deferral" in allowed

    assert _admit_entity_key("k401_employee_deferral", allowed) == "k401_employee_deferral"
    assert _admit_entity_key("  hsa_family  ", allowed) == "hsa_family"
    assert _admit_entity_key(None, allowed) is None
    # an invented key, a near-miss, and a plausible-but-absent key all collapse to None
    assert _admit_entity_key("k401_combined_limit", allowed) is None
    assert _admit_entity_key("K401_EMPLOYEE_DEFERRAL", allowed) is None
    assert _admit_entity_key("hce_threshold", allowed) is None


def test_system_prompt_carries_every_catalog_key_with_its_label():
    catalog = load_catalog()
    prompt = build_system_prompt()
    assert len(catalog) == 15
    for key, entry in catalog.items():
        assert key in prompt, f"{key} missing from the closed vocabulary in the prompt"
        assert entry.label in prompt, f"label for {key} missing from the prompt"


def test_system_prompt_contains_no_verdict_vocabulary():
    """The inviolable principle, as a test.

    extract.py labels what a sentence IS. judge.py decides what it MEANS. A prompt that asks the
    model whether a number is out of date moves the decision into the half of the system that
    cannot carry a `rule_fired`, and is wrong even when its output looks right.
    """
    prompt = build_system_prompt().lower()
    forbidden = [
        "stale", "out of date", "outdated", "no longer", "correct", "incorrect",
        "material", "wrong", "accurate", "inaccurate", "up to date", "current value",
        "should be updated", "needs updating", "verdict",
    ]
    hits = [word for word in forbidden if word in prompt]
    assert not hits, f"verdict language leaked into the extraction prompt: {hits}"


def test_claim_type_vocabulary_is_the_seven_labels_and_the_prompt_teaches_every_one():
    """models.py locks after A-03, so the vocabulary is pinned here.

    `structural` is the seventh label, added on Zaeem's instruction (finding F-1) because
    evals/claims.jsonl already used it and the six-label set collapsed those rows into `other`.
    """
    import typing

    from almanac.models import ClaimType

    labels = typing.get_args(ClaimType)
    assert labels == (
        "statutory_limit", "tax_bracket", "market_rate",
        "illustrative", "historical", "structural", "other",
    )

    prompt = build_system_prompt()
    for label in labels:
        assert f"{label} " in prompt, f"the prompt never defines {label}"


def test_structural_is_distinguished_from_other_in_the_prompt():
    prompt = build_system_prompt()
    assert "you cannot touch the money for twelve months" in prompt
    assert "rule about HOW a product or account works" in prompt


def test_every_claim_type_in_the_eval_set_is_in_the_vocabulary():
    """A label A-02 uses that A-03 cannot express is a silent downgrade to `other`."""
    import typing

    from almanac.models import ClaimType

    rows = [
        json.loads(line)
        for line in (REPO_ROOT / "evals/claims.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 54
    labels = set(typing.get_args(ClaimType))
    unexpressible = sorted({r["expected_claim_type"] for r in rows} - labels)
    assert not unexpressible, f"eval labels A-03 cannot express: {unexpressible}"


def test_the_three_way_distinction_is_taught_with_examples():
    prompt = build_system_prompt()
    assert "the 401(k) limit is $23,000" in prompt
    assert "say you contribute $23,000" in prompt
    assert 'in 2022 it paid 9.62%' in prompt


# ------------------------------------------------------------------------------- chunking


def test_chunks_are_cut_on_locator_boundaries_and_lose_no_text():
    source = read_srt(V1)
    chunks = chunk(source, chunk_chars=400)

    assert len(chunks) > 1, "the fixture is too small to exercise multi-chunk behaviour"
    boundaries = {loc.start for loc in source.locators}
    assert all(base in boundaries for base, _ in chunks)
    assert sum(len(text) for _, text in chunks) + (len(chunks) - 1) == len(source.text)


def test_a_quote_found_in_a_chunk_maps_back_to_the_right_locator():
    """This is what 'locators preserved' means: chunk-local offset + base == document offset."""
    source = read_srt(V1)
    quote = "the catch up is seven thousand five hundred dollars"
    chunks = chunk(source, chunk_chars=400)

    hits = [(base, text.find(quote)) for base, text in chunks if quote in text]
    assert len(hits) == 1
    base, local = hits[0]
    global_span = locate(source.text, quote)
    assert global_span is not None
    assert base + local == global_span[0]
    assert locator_for(source.locators, base + local) == "00:00:30,000"


def test_default_chunk_size_keeps_a_caption_track_in_one_call():
    source = read_srt(V1)
    assert len(chunk(source)) == 1


def test_locator_for_falls_back_to_the_preceding_span():
    locators = [Locator(0, 5, "A"), Locator(10, 15, "B")]
    assert locator_for(locators, 2) == "A"
    assert locator_for(locators, 7) == "A"
    assert locator_for(locators, 12) == "B"


# ---------------------------------------------------- assembly: what the CODE decides


def _extracted(**overrides) -> ExtractedClaim:
    payload = {
        "quote": "the catch up is seven thousand five hundred dollars",
        "claim_type": "statutory_limit",
        "entity_key": "k401_catchup_50plus",
        "value": 7500.0,
        "unit": "usd",
        "year_hint": 2024,
        "confidence": 0.9,
    }
    payload.update(overrides)
    return ExtractedClaim(**payload)


def test_assemble_attaches_provenance_the_model_never_supplied():
    source = read_srt(V1)
    stats = ExtractionStats()
    claims = _assemble(source, [_extracted()], set(load_catalog().keys()), stats)

    assert len(claims) == 1
    claim = claims[0]
    assert claim.source_id == source.source_id
    assert claim.locator == "00:00:30,000"
    assert claim.quote in source.text
    assert claim.entity_key == "k401_catchup_50plus"
    assert claim.value == 7500.0
    assert claim.year_hint == 2024
    assert stats.returned == 1 and stats.dropped_unlocatable == 0


def test_assemble_drops_a_hallucinated_quote_and_counts_the_drop():
    source = read_srt(V1)
    stats = ExtractionStats()
    claims = _assemble(
        source,
        [_extracted(quote="the catch up is nine thousand dollars"), _extracted()],
        set(load_catalog().keys()),
        stats,
    )

    assert len(claims) == 1
    assert stats.returned == 2
    assert stats.dropped_unlocatable == 1


def test_assemble_demotes_an_off_vocabulary_key_and_keeps_the_claim():
    """Test the branch where entity_key is PRESENT but wrong — not only where it is absent."""
    source = read_srt(V1)
    stats = ExtractionStats()
    claims = _assemble(
        source, [_extracted(entity_key="k401_combined_limit")], set(load_catalog().keys()), stats
    )

    assert len(claims) == 1
    assert claims[0].entity_key is None
    assert claims[0].claim_type == "statutory_limit"
    assert stats.demoted_entity_key == 1


def test_assemble_deduplicates_the_same_span():
    source = read_srt(V1)
    stats = ExtractionStats()
    claims = _assemble(source, [_extracted(), _extracted()], set(load_catalog().keys()), stats)
    assert len(claims) == 1
    assert stats.dropped_duplicate == 1


def test_assemble_returns_claims_in_document_order():
    source = read_srt(V1)
    later = _extracted(quote="the catch up is seven thousand five hundred dollars")
    earlier = _extracted(quote="you can defer twenty three thousand dollars", entity_key=None)
    claims = _assemble(source, [later, earlier], set(load_catalog().keys()), ExtractionStats())

    positions = [source.text.find(c.quote) for c in claims]
    assert positions == sorted(positions)


def test_assemble_clamps_confidence_into_range():
    source = read_srt(V1)
    claims = _assemble(source, [_extracted(confidence=1.7)], set(load_catalog().keys()), ExtractionStats())
    assert claims[0].confidence == 1.0


def test_assemble_trims_an_over_long_quote_and_it_stays_verbatim():
    source = read_srt(V1)
    long_quote = source.text[100:400]
    claims = _assemble(
        source, [_extracted(quote=long_quote)], set(load_catalog().keys()), ExtractionStats()
    )
    assert len(claims[0].quote) <= 200
    assert claims[0].quote in source.text


# ------------------------------------------------------------------ the tool schema itself


def test_the_tool_schema_is_the_nested_defs_form_adr_000_proved_accepted():
    schema = Extraction.model_json_schema()
    assert "$defs" in schema
    assert schema["properties"]["claims"]["items"]["$ref"] == "#/$defs/ExtractedClaim"


def test_the_model_is_never_asked_for_provenance():
    """A field the model cannot fill is a field it cannot get wrong."""
    fields = Extraction.model_json_schema()["$defs"]["ExtractedClaim"]["properties"]
    assert "source_id" not in fields
    assert "locator" not in fields
    assert set(fields) == {"quote", "claim_type", "entity_key", "value", "unit", "year_hint", "confidence"}


def test_claim_model_enforces_the_shape_the_issue_specifies():
    with pytest.raises(Exception):
        Claim(source_id="s", locator="L1", quote="x" * 201, claim_type="other", confidence=0.5)
    with pytest.raises(Exception):
        Claim(source_id="s", locator="L1", quote="x", claim_type="other", confidence=1.4)
    with pytest.raises(Exception):
        Claim(source_id="s", locator="L1", quote="x", claim_type="not_a_type", confidence=0.5)


# ------------------------------------------------------- the one-claim-per-number rule (D-2)


def test_prompt_asks_for_one_claim_per_number_not_per_sentence():
    """ADR-002 D-2: "One claim per sentence-with-a-number" is what merged multi-number sentences.

    Pinned as a test because it is a prompt line with no other guard: nothing else in the suite
    fails if it silently reverts, and its effect is only visible in a live run.
    """
    from almanac.extract import build_system_prompt

    prompt = build_system_prompt()
    assert "ONE CLAIM PER NUMBER" in prompt
    assert "One claim per sentence-with-a-number" not in prompt, "the merging instruction is back"


def test_prompt_tells_the_model_to_quote_the_clause_when_a_sentence_has_several_numbers():
    """Without this, one-claim-per-number yields duplicate whole-sentence quotes that _assemble
    dedupes by span — turning the fix into a no-op."""
    from almanac.extract import build_system_prompt

    prompt = build_system_prompt()
    assert "quote the CLAUSE around the number" in prompt
    assert "must not carry the same quote" in prompt
