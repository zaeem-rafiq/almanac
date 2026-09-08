"""Scores the pipeline against the human labels in `evals/claims.jsonl`.

RULE: this module READS the labelled set. It never writes it. `evals/claims.jsonl` is Zaeem's
evidence, and an agent that edits a label to move a number is falsifying the thing the demo rests
on. A miss is *presented*, never absorbed. The same applies to `judge.MARKET_RATE_MATERIAL_PP`:
nothing here reads it, and nothing here may tune it.

No model is called from this module. It orchestrates `extract` (which calls one) and `judge`
(which never does), then does arithmetic.

## Matching — normalised quote overlap

A labelled row and a produced verdict are the same claim when the verdict's quote **covers** the
labelled quote. Both are located in the source's *normalised* text — `extract._normalise`, which
collapses whitespace runs and straightens curly quotes and nothing else. That is deliberately the
same leniency extraction already allows for the verbatim rule, so matching can never be looser
than the guarantee the quote itself carries.

Coverage, not intersection-over-union, because the two sides are not trying to be the same string.
A label is a short fragment a human chose to identify a sentence ("Say you make seventy thousand
dollars"); an extracted quote is the whole sentence the claim lives in ("Say you make seventy
thousand dollars and your plan matches six percent of your salary."). IoU scores that pair 0.43
and calls it a miss, which blames extraction for a claim it did in fact produce. The question the
metric has to answer is "did the pipeline produce a verdict for this labelled claim?", and
containment answers it; span-identity answers a different question nobody asked.

Measured, not assumed: across the 54 labelled rows the best coverage is **exactly 1.00 for 51 of
them**, then a cliff to 0.67. Any floor in (0.67, 1.00] selects the same 51 rows, which is why
`sensitivity` sweeps the floor and the report prints the result — a criterion that moves with its
threshold is a criterion doing the work of a conclusion. See ADR-002 D-1.

Ranking is by coverage, then by IoU, so a label covered by two verdicts is paired with the
tighter one rather than an arbitrary one. Assignment is greedy and **one-to-one**, so two labels
can never both claim the same verdict and inflate recall.

`scripts/score_a04.py` matched with `source.text.find(candidate.quote)` and took the *first*
overlapping candidate. That locates the first textual occurrence rather than the claim's own span,
and picks an arbitrary candidate rather than the best one. Neither behaviour is inherited here.

## The denominators are the honest ones

`recall` for a status counts **every labelled row of that status**, including rows extraction never
produced a claim for. A `stale_material` sentence the extractor walked past is a video that stays
wrong — scoring recall over matched rows only would hide exactly the failure this product exists
to catch. `precision` can only be computed over matched rows, since an unmatched verdict has no
label to be right or wrong about; the count of those is reported separately rather than assumed
to be zero.

## Liveness comes before the negative

"Zero hard-negative false positives" is satisfied perfectly by a pipeline that produced nothing.
`gate_failures` therefore checks, in order, that sources were read, that verdicts were produced,
and that rows were matched — and only then that none of them are false positives.
"""

from __future__ import annotations

import json
import typing
from dataclasses import dataclass, field
from pathlib import Path

from almanac.extract import Source, _normalise, discover_sources, extract_sources
from almanac.judge import Status, Verdict, judge_all
from almanac.models import Claim, ClaimType

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAIMS_PATH = REPO_ROOT / "evals" / "claims.jsonl"
DEFAULT_OUT_DIR = REPO_ROOT / "evals"

STATUSES: tuple[str, ...] = typing.get_args(Status)
CLAIM_TYPES: tuple[str, ...] = typing.get_args(ClaimType)

STALE_STATUSES = frozenset({"stale_material", "stale_immaterial"})

#: The claim types a viewer would be actively misled by a correction on. `illustrative` is a
#: worked example ("say you put in ten thousand dollars"); `historical` is a sentence the speaker
#: framed as past ("back in 2022 it paid nine point six two"). Both are accurate as spoken, so a
#: `stale_*` verdict on either is a confident false correction — the failure mode Almanac exists
#: to prevent, and the one the binary gate refuses to ship with.
HARD_NEGATIVE_CLAIM_TYPES = frozenset({"illustrative", "historical"})

#: A verdict matches a labelled row when its quote covers at least this much of the labelled quote.
#: NOT fitted to the labelled set. The observed distribution is 51 rows at coverage exactly 1.00
#: and then a cliff to 0.67, so every floor in (0.67, 1.00] selects the same rows. `sensitivity`
#: re-runs the match across the sweep below and the report prints every count: the 0.50 probe is
#: there so a constant result cannot be mistaken for a sweep that is incapable of moving.
MIN_LABEL_COVERAGE = 0.90
SENSITIVITY_THRESHOLDS = (0.50, 0.70, 0.90, 1.00)

#: Floors at or above this are the band the harness could defensibly operate in. The 0.50 entry
#: sits below it as a deliberate probe: a sweep that reports "constant" is only evidence when the
#: sweep is capable of moving at all, so the report says whether it moved anywhere.
SENSITIVITY_BAND_FLOOR = 0.70

#: The gate. Both are the issue's, not this module's to relax.
STALE_MATERIAL_RECALL_FLOOR = 0.90
HARD_NEGATIVE_FP_CEILING = 0

#: What a metric shows when extraction produced no claim for a labelled row at all.
NOT_EXTRACTED = "<not extracted>"


# --------------------------------------------------------------------------------- the labels


@dataclass(frozen=True)
class Label:
    """One row of `evals/claims.jsonl`, with the line it came from so a miss can be pointed at."""

    line: int
    source: str
    quote: str
    expected_claim_type: str
    expected_entity_key: str | None
    expected_status: str

    @property
    def is_hard_negative(self) -> bool:
        return self.expected_claim_type in HARD_NEGATIVE_CLAIM_TYPES


def load_labels(path: str | Path = CLAIMS_PATH) -> list[Label]:
    """Read the labelled set. Read-only, always: nothing in this package writes this file."""
    labels: list[Label] = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        labels.append(Label(
            line=number,
            source=row["source"],
            quote=row["quote"],
            expected_claim_type=row["expected_claim_type"],
            expected_entity_key=row.get("expected_entity_key"),
            expected_status=row["expected_status"],
        ))
    return labels


# ------------------------------------------------------------------------------- the matching


def normalised_span(source_text: str, quote: str, normal_text: str | None = None) -> tuple[int, int] | None:
    """Locate `quote` in `source_text` in normalised space, or None when it is not there.

    Offsets are in the NORMALISED string, which is the only space the label and the claim can be
    compared in — they came from different producers and differ in whitespace, not in words.
    """
    if normal_text is None:
        normal_text, _ = _normalise(source_text)
    normal_quote, _ = _normalise(quote)
    if not normal_quote:
        return None
    position = normal_text.find(normal_quote)
    if position < 0:
        return None
    return position, position + len(normal_quote)


def iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Intersection over union of two character spans. 0.0 when they do not overlap."""
    overlap = min(a[1], b[1]) - max(a[0], b[0])
    if overlap <= 0:
        return 0.0
    union = (a[1] - a[0]) + (b[1] - b[0]) - overlap
    return overlap / union if union > 0 else 0.0


def coverage(label_span: tuple[int, int], verdict_span: tuple[int, int]) -> float:
    """How much of the LABELLED span the verdict's span contains, in [0, 1].

    Asymmetric on purpose. The denominator is the label alone, so a verdict quoting a longer
    sentence that fully contains the labelled fragment scores 1.00 — which is the correct answer
    to "was this labelled claim produced?" — while a verdict quoting only part of it cannot.
    """
    width = label_span[1] - label_span[0]
    if width <= 0:
        return 0.0
    overlap = min(label_span[1], verdict_span[1]) - max(label_span[0], verdict_span[0])
    return max(0, overlap) / width


@dataclass(frozen=True)
class Match:
    """A labelled row paired with the verdict that covers it."""

    label: Label
    verdict: Verdict
    coverage: float
    overlap: float

    @property
    def status_agrees(self) -> bool:
        return self.verdict.status == self.label.expected_status

    @property
    def claim_type_agrees(self) -> bool:
        return self.verdict.claim.claim_type == self.label.expected_claim_type

    @property
    def is_hard_negative_false_positive(self) -> bool:
        """An illustrative or historical sentence the judge called stale. The gated failure."""
        return self.label.is_hard_negative and self.verdict.status in STALE_STATUSES


def match(
    labels: list[Label],
    verdicts_by_source: dict[str, list[Verdict]],
    sources: dict[str, Source],
    min_coverage: float = MIN_LABEL_COVERAGE,
) -> tuple[list[Match], list[Label], list[Label], list[Verdict]]:
    """Pair labels with verdicts by normalised quote coverage, one-to-one.

    Returns `(matches, unmatched_labels, unlocatable_labels, unmatched_verdicts)`.

    A label whose own quote cannot be found in its own source is *unlocatable* — that is the
    labelled set and the corpus having drifted apart, not a miss by the pipeline, and it is
    reported as its own category so it can never be read as a scoring result.
    """
    normal_cache = {sid: _normalise(src.text)[0] for sid, src in sources.items()}

    unlocatable: list[Label] = []
    label_spans: dict[int, tuple[int, int]] = {}
    for index, label in enumerate(labels):
        source = sources.get(label.source)
        span = None
        if source is not None:
            span = normalised_span(source.text, label.quote, normal_cache[label.source])
        if span is None:
            unlocatable.append(label)
        else:
            label_spans[index] = span

    verdict_spans: dict[tuple[str, int], tuple[int, int]] = {}
    for source_id, verdicts in verdicts_by_source.items():
        source = sources.get(source_id)
        if source is None:
            continue
        for position, verdict in enumerate(verdicts):
            span = normalised_span(source.text, verdict.claim.quote, normal_cache[source_id])
            if span is not None:
                verdict_spans[(source_id, position)] = span

    candidates: list[tuple[float, float, int, tuple[str, int]]] = []
    for index, label_span in label_spans.items():
        for key, verdict_span in verdict_spans.items():
            if key[0] != labels[index].source:
                continue
            covered = coverage(label_span, verdict_span)
            if covered >= min_coverage:
                candidates.append((covered, iou(label_span, verdict_span), index, key))

    # Greedy one-to-one: best coverage wins, ties broken by the tighter quote, and both sides are
    # then spent. A many-to-one assignment would let one verdict satisfy several labels and
    # inflate recall — see `test_one_verdict_cannot_satisfy_two_labels`.
    candidates.sort(key=lambda c: (-c[0], -c[1], c[2], c[3]))
    taken_labels: set[int] = set()
    taken_verdicts: set[tuple[str, int]] = set()
    matches: list[Match] = []
    for covered, overlap, index, key in candidates:
        if index in taken_labels or key in taken_verdicts:
            continue
        taken_labels.add(index)
        taken_verdicts.add(key)
        matches.append(Match(labels[index], verdicts_by_source[key[0]][key[1]], covered, overlap))

    matches.sort(key=lambda m: (m.label.source, m.label.line))
    unmatched_labels = [
        label for index, label in enumerate(labels)
        if index not in taken_labels and label not in unlocatable
    ]
    unmatched_verdicts = [
        verdict
        for source_id, verdicts in verdicts_by_source.items()
        for position, verdict in enumerate(verdicts)
        if (source_id, position) not in taken_verdicts
    ]
    return matches, unmatched_labels, unlocatable, unmatched_verdicts


# -------------------------------------------------------------------------------- the metrics


@dataclass(frozen=True)
class StatusScore:
    """Precision and recall for one status, each with the denominator it was computed over."""

    status: str
    true_positives: int
    false_positives: int
    false_negatives: int
    support: int
    not_extracted: int

    @property
    def precision(self) -> float | None:
        predicted = self.true_positives + self.false_positives
        return self.true_positives / predicted if predicted else None

    @property
    def recall(self) -> float | None:
        return self.true_positives / self.support if self.support else None


@dataclass
class EvalResult:
    """Everything one run measured. Every count carries what it was counted out of."""

    labels: list[Label]
    matches: list[Match]
    unmatched_labels: list[Label]
    unlocatable_labels: list[Label]
    unmatched_verdicts: list[Verdict]
    sources_read: list[str]
    verdicts_produced: int
    min_coverage: float
    sensitivity: dict[float, int] = field(default_factory=dict)
    cached_extraction: bool = False

    # ---- (a) extraction recall

    @property
    def matched(self) -> int:
        return len(self.matches)

    @property
    def extraction_recall(self) -> float | None:
        total = len(self.labels)
        return self.matched / total if total else None

    # ---- (b) claim_type accuracy

    @property
    def claim_type_hits(self) -> int:
        return sum(1 for m in self.matches if m.claim_type_agrees)

    @property
    def claim_type_accuracy(self) -> float | None:
        return self.claim_type_hits / self.matched if self.matched else None

    def claim_type_confusion(self) -> dict[tuple[str, str], int]:
        """(expected, got) counts. `structural` is a first-class key, never folded into 'other'."""
        confusion: dict[tuple[str, str], int] = {}
        for m in self.matches:
            key = (m.label.expected_claim_type, m.verdict.claim.claim_type)
            confusion[key] = confusion.get(key, 0) + 1
        return confusion

    def claim_types_seen(self) -> list[str]:
        seen = {m.label.expected_claim_type for m in self.matches}
        seen |= {m.verdict.claim.claim_type for m in self.matches}
        seen |= {label.expected_claim_type for label in self.labels}
        ordered = [t for t in CLAIM_TYPES if t in seen]
        return ordered + sorted(seen - set(CLAIM_TYPES))

    # ---- (c) per-status precision / recall

    def status_scores(self) -> list[StatusScore]:
        scores: list[StatusScore] = []
        for status in STATUSES:
            tp = sum(1 for m in self.matches if m.label.expected_status == status and m.verdict.status == status)
            fp = sum(1 for m in self.matches if m.label.expected_status != status and m.verdict.status == status)
            matched_fn = sum(
                1 for m in self.matches if m.label.expected_status == status and m.verdict.status != status
            )
            # A labelled row extraction never produced a claim for is a recall miss, not an
            # absent row. Counting it anywhere else would let the extractor improve a score by
            # finding fewer claims.
            missing = sum(1 for label in self.unmatched_labels if label.expected_status == status)
            support = sum(1 for label in self.labels if label.expected_status == status)
            scores.append(StatusScore(status, tp, fp, matched_fn + missing, support, missing))
        return scores

    def status_score(self, status: str) -> StatusScore:
        return next(s for s in self.status_scores() if s.status == status)

    def status_confusion(self) -> dict[tuple[str, str], int]:
        confusion: dict[tuple[str, str], int] = {}
        for m in self.matches:
            key = (m.label.expected_status, m.verdict.status)
            confusion[key] = confusion.get(key, 0) + 1
        for label in self.unmatched_labels:
            key = (label.expected_status, NOT_EXTRACTED)
            confusion[key] = confusion.get(key, 0) + 1
        return confusion

    # ---- (d) the hard negatives

    @property
    def hard_negative_labels(self) -> list[Label]:
        return [label for label in self.labels if label.is_hard_negative]

    @property
    def hard_negative_matches(self) -> list[Match]:
        return [m for m in self.matches if m.label.is_hard_negative]

    @property
    def hard_negative_false_positives(self) -> list[Match]:
        return [m for m in self.matches if m.is_hard_negative_false_positive]

    @property
    def unlabelled_stale_verdicts(self) -> list[Verdict]:
        """Stale verdicts on sentences the eval set does not cover — observability, not a gate.

        The issue defines the hard-negative count over labelled rows. A stale verdict outside the
        labelled set cannot be scored against a label, but a growing number here means the eval
        set has stopped covering what the pipeline actually emits, so it is printed rather than
        left invisible.
        """
        return [v for v in self.unmatched_verdicts if v.status in STALE_STATUSES]

    # ---- (e) the misses

    def misses(self) -> list[Match]:
        return [m for m in self.matches if not m.status_agrees]

    def claim_type_misses(self) -> list[Match]:
        return [m for m in self.matches if not m.claim_type_agrees]


# ----------------------------------------------------------------------------------- the gate


def gate_failures(result: EvalResult) -> list[str]:
    """Return every reason this run must exit non-zero. Empty list means the gate passed.

    Liveness first, on purpose: a hard-negative count of zero is what an empty run produces, so
    the pipeline is proved to have been alive before any negative is believed.
    """
    failures: list[str] = []

    if not result.sources_read:
        failures.append("no sources were read — the run was empty, not clean")
        return failures
    if result.verdicts_produced == 0:
        failures.append(
            f"0 verdicts produced from {len(result.sources_read)} source(s) — "
            "the pipeline did not run, so every negative below is vacuous"
        )
        return failures
    if result.matched == 0:
        failures.append(
            f"0 of {len(result.labels)} labelled rows matched a verdict — "
            f"{result.verdicts_produced} verdict(s) exist but none could be paired"
        )
        return failures
    if result.unlocatable_labels:
        failures.append(
            f"{len(result.unlocatable_labels)} labelled quote(s) not present in their own source "
            "— evals/claims.jsonl and corpus/ have drifted apart"
        )

    false_positives = result.hard_negative_false_positives
    if len(false_positives) > HARD_NEGATIVE_FP_CEILING:
        failures.append(
            f"{len(false_positives)} hard-negative false positive(s) over "
            f"{len(result.hard_negative_matches)} matched illustrative/historical row(s) "
            f"(ceiling {HARD_NEGATIVE_FP_CEILING})"
        )

    stale = result.status_score("stale_material")
    if stale.recall is None:
        failures.append("stale_material recall is undefined — the labelled set carries no such row")
    elif stale.recall < STALE_MATERIAL_RECALL_FLOOR:
        failures.append(
            f"stale_material recall {stale.recall:.2f} < {STALE_MATERIAL_RECALL_FLOOR:.2f} "
            f"({stale.true_positives}/{stale.support})"
        )
    return failures


# ------------------------------------------------------------------------------- the run


def _cache_read(path: Path) -> dict[str, list[Claim]] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {sid: [Claim.model_validate(c) for c in claims] for sid, claims in payload.items()}


def _cache_write(path: Path, by_source: dict[str, list[Claim]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {sid: [c.model_dump(mode="json") for c in claims] for sid, claims in by_source.items()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(
    claims_path: str | Path = CLAIMS_PATH,
    min_coverage: float = MIN_LABEL_COVERAGE,
    cache_path: str | Path | None = None,
) -> EvalResult:
    """extract -> judge -> match -> score, over every source the labelled set references.

    `cache_path` persists one extraction so a re-run during the miss review scores the SAME
    claims. `docs/proofs/A-04.md` records 56-67 verdicts across otherwise identical runs, so
    without it a rule change and a resample are indistinguishable. The gate run is uncached.
    """
    labels = load_labels(claims_path)
    source_ids = sorted({label.source for label in labels})

    sources: dict[str, Source] = {}
    for source_id in source_ids:
        found = discover_sources(REPO_ROOT / source_id)
        if found:
            sources[source_id] = found[0]

    cache = Path(cache_path) if cache_path else None
    claims_by_source = _cache_read(cache) if cache else None
    cached = claims_by_source is not None
    if claims_by_source is None:
        claims_by_source = extract_sources(list(sources.values()))
        if cache:
            _cache_write(cache, claims_by_source)

    verdicts_by_source = {
        source_id: judge_all(claims_by_source.get(source_id, []))
        for source_id in sources
    }
    verdicts_produced = sum(len(v) for v in verdicts_by_source.values())

    matches, unmatched, unlocatable, spare = match(labels, verdicts_by_source, sources, min_coverage)
    sweep = {
        threshold: len(match(labels, verdicts_by_source, sources, threshold)[0])
        for threshold in SENSITIVITY_THRESHOLDS
    }

    return EvalResult(
        labels=labels,
        matches=matches,
        unmatched_labels=unmatched,
        unlocatable_labels=unlocatable,
        unmatched_verdicts=spare,
        sources_read=sorted(sources),
        verdicts_produced=verdicts_produced,
        min_coverage=min_coverage,
        sensitivity=sweep,
        cached_extraction=cached,
    )


# ------------------------------------------------------------------------------- the reporting


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _ratio(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _clip(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _read_of(match: Match) -> str:
    """A one-line read of which side is likely wrong, so the review can be fast.

    This is a POINTER, not a verdict on the label. It never edits anything and it is explicitly
    framed as a suggestion for Zaeem, because deciding whether the label or the system is wrong
    is his call — an agent that decides it silently is tuning the evidence.
    """
    expected, got = match.label.expected_status, match.verdict.status
    rule = match.verdict.rule_fired
    if match.is_hard_negative_false_positive:
        return "SYSTEM — a stale verdict on a sentence that is accurate as spoken"
    if not match.claim_type_agrees:
        return (f"EXTRACTION — labelled {match.label.expected_claim_type}, model said "
                f"{match.verdict.claim.claim_type}; the type choice drove the status")
    if got == "unresolved" and rule.startswith("R3"):
        return "COVERAGE — the judge had no fact to resolve against; catalog or entity_key, not a rule"
    if expected == "unresolved" and got != "unresolved":
        return "LABEL or COVERAGE — the label expects no resolution but the catalog resolved it"
    if {expected, got} <= STALE_STATUSES:
        return "THRESHOLD — same divergence, different materiality band; do not tune to this row"
    if got == "skip":
        return "EXTRACTION — the claim was triaged out before any fact was consulted"
    return "REVIEW — status differs with matching claim_type; read the detail"


def render_markdown(result: EvalResult, run_at: str | None = None) -> str:
    """The report, as Markdown. Printed to the transcript AND written to `evals/results.md`.

    The console output and the file are the same string on purpose: two renderers is two chances
    for the printed number to disagree with the written one.
    """
    from datetime import datetime, timezone

    stamp = run_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    failures = gate_failures(result)
    lines: list[str] = []
    add = lines.append

    add("# Almanac eval — extract + judge vs. the labelled set")
    add("")
    add(f"`python -m almanac eval` · {stamp}")
    add("")
    add(f"**GATE: {'PASS' if not failures else 'FAIL'}**")
    if failures:
        add("")
        for failure in failures:
            add(f"- FAIL — {failure}")
    add("")

    # Liveness first: every negative below is only meaningful if these are non-zero.
    add("## Run")
    add("")
    add("| | |")
    add("|---|---|")
    add(f"| sources read | {len(result.sources_read)} |")
    add(f"| verdicts produced | {result.verdicts_produced} |")
    add(f"| labelled rows | {len(result.labels)} |")
    add(f"| **rows matched** | **{result.matched} / {len(result.labels)}** |")
    add(f"| labelled rows with no claim | {len(result.unmatched_labels)} |")
    add(f"| labelled quotes absent from their source | {len(result.unlocatable_labels)} |")
    add(f"| verdicts with no labelled row | {len(result.unmatched_verdicts)} |")
    add(f"| extraction | {'cached' if result.cached_extraction else 'live'} |")
    add("")
    add(f"Matching: a verdict's quote covers ≥ {result.min_coverage:.2f} of the labelled quote, "
        "greedy and one-to-one, ties broken by the tighter quote.")
    if result.sensitivity:
        sweep = " · ".join(f"≥{t:.2f} → {n}" for t, n in sorted(result.sensitivity.items()))
        band = {t: n for t, n in result.sensitivity.items() if t >= SENSITIVITY_BAND_FLOOR}
        moves_in_band = len(set(band.values())) > 1
        moves_anywhere = len(set(result.sensitivity.values())) > 1
        add("")
        add(f"Coverage-floor sensitivity (matched rows): {sweep}.")
        if moves_in_band:
            add(f"**The count moves inside the operating band (≥ {SENSITIVITY_BAND_FLOOR:.2f}) — "
                "the floor is load-bearing and the choice belongs in the ADR.**")
        else:
            settled = next(iter(band.values())) if band else 0
            line = (f"Constant at {settled} across the operating band "
                    f"(≥ {SENSITIVITY_BAND_FLOOR:.2f}), so the floor is not doing the work.")
            if moves_anywhere:
                line += (f" The sub-band probe at ≥ {min(result.sensitivity):.2f} returns "
                         f"{result.sensitivity[min(result.sensitivity)]}, which shows the sweep "
                         "can move — a constant band is evidence, not a metric that never varies.")
            else:
                line += (" **The sweep never moves at any floor, so it cannot detect a "
                         "load-bearing threshold — treat it as uninformative.**")
            add(line)
    add("")

    # (a)
    add("## (a) Extraction recall")
    add("")
    add(f"**{_pct(result.extraction_recall)}** — {result.matched} of {len(result.labels)} "
        "labelled claims were found by `extract.py`.")
    add("")

    # (b)
    add("## (b) claim_type accuracy")
    add("")
    add(f"**{_pct(result.claim_type_accuracy)}** — {result.claim_type_hits} of {result.matched} "
        "matched rows carry the labelled type.")
    add("")
    confusion = result.claim_type_confusion()
    seen = result.claim_types_seen()
    if seen:
        add("| expected \\ got | " + " | ".join(f"`{t}`" for t in seen) + " | total |")
        add("|---" * (len(seen) + 2) + "|")
        for expected in seen:
            cells = [str(confusion.get((expected, got), 0) or "·") for got in seen]
            total = sum(confusion.get((expected, got), 0) for got in seen)
            add(f"| `{expected}` | " + " | ".join(cells) + f" | {total} |")
    add("")

    # (c)
    add("## (c) Per-status precision / recall")
    add("")
    add("Recall counts **every labelled row of that status**, including rows extraction never "
        "produced a claim for — a missed stale sentence is a video that stays wrong. Precision is "
        "over matched rows only, since an unmatched verdict has no label to be scored against.")
    add("")
    add("| status | precision | recall | TP | FP | FN | support | of which not extracted |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|")
    for score in result.status_scores():
        add(f"| `{score.status}` | {_ratio(score.precision)} | {_ratio(score.recall)} | "
            f"{score.true_positives} | {score.false_positives} | {score.false_negatives} | "
            f"{score.support} | {score.not_extracted} |")
    add("")
    status_confusion = result.status_confusion()
    columns = list(STATUSES) + [NOT_EXTRACTED]
    add("| expected \\ got | " + " | ".join(f"`{s}`" for s in columns) + " |")
    add("|---" * (len(columns) + 1) + "|")
    for expected in STATUSES:
        cells = [str(status_confusion.get((expected, got), 0) or "·") for got in columns]
        add(f"| `{expected}` | " + " | ".join(cells) + " |")
    add("")

    # (d)
    add("## (d) Hard negatives")
    add("")
    false_positives = result.hard_negative_false_positives
    add(f"**{len(false_positives)} illustrative/historical row(s) wrongly flagged as stale** "
        f"— out of {len(result.hard_negative_matches)} matched "
        f"(of {len(result.hard_negative_labels)} labelled hard negatives). "
        f"Ceiling {HARD_NEGATIVE_FP_CEILING}.")
    add("")
    if false_positives:
        add("| line | claim_type | got | rule_fired | quote |")
        add("|---|---|---|---|---|")
        for m in false_positives:
            add(f"| {m.label.line} | `{m.label.expected_claim_type}` | `{m.verdict.status}` | "
                f"`{m.verdict.rule_fired}` | {_clip(m.label.quote, 70)} |")
        add("")
    spare_stale = result.unlabelled_stale_verdicts
    add(f"Stale verdicts outside the labelled set: {len(spare_stale)} "
        "(not gated — the issue defines the hard-negative count over labelled rows — but a rising "
        "number means the eval set has stopped covering what the pipeline emits).")
    add("")

    # (e)
    add("## (e) Misses")
    add("")
    misses = result.misses()
    if not misses and not result.unmatched_labels:
        add("None. Every labelled row matched a verdict and every status agrees.")
    else:
        add(f"{len(misses)} status disagreement(s) on matched rows, plus "
            f"{len(result.unmatched_labels)} labelled row(s) extraction produced no claim for.")
        add("")
    if misses:
        add("| line | expected | got | rule_fired | claim_type (exp → got) | quote | read |")
        add("|---|---|---|---|---|---|---|")
        for m in misses:
            types = (f"`{m.label.expected_claim_type}` → `{m.verdict.claim.claim_type}`"
                     if not m.claim_type_agrees else f"`{m.label.expected_claim_type}` ✓")
            add(f"| {m.label.line} | `{m.label.expected_status}` | `{m.verdict.status}` | "
                f"`{m.verdict.rule_fired}` | {types} | {_clip(m.label.quote, 60)} | {_read_of(m)} |")
        add("")
        add("Detail for each miss:")
        add("")
        for m in misses:
            add(f"- **line {m.label.line}** ({m.label.source}, `{m.verdict.claim.locator}`, "
                f"coverage {m.coverage:.2f}) — expected `{m.label.expected_status}`, got "
                f"`{m.verdict.status}` via `{m.verdict.rule_fired}`")
            add(f"  - quote: “{_clip(m.label.quote, 150)}”")
            add(f"  - judge said: {m.verdict.detail}")
            add(f"  - read: {_read_of(m)}")
        add("")
    if result.unmatched_labels:
        add("Labelled rows with no claim produced:")
        add("")
        add("| line | expected | claim_type | quote |")
        add("|---|---|---|---|")
        for label in result.unmatched_labels:
            add(f"| {label.line} | `{label.expected_status}` | `{label.expected_claim_type}` | "
                f"{_clip(label.quote, 70)} |")
        add("")

    type_misses = result.claim_type_misses()
    if type_misses:
        add("### claim_type disagreements (status may still agree)")
        add("")
        add("| line | expected | got | status | quote |")
        add("|---|---|---|---|---|")
        for m in type_misses:
            add(f"| {m.label.line} | `{m.label.expected_claim_type}` | "
                f"`{m.verdict.claim.claim_type}` | `{m.verdict.status}`"
                f"{'' if m.status_agrees else ' ⚠'} | {_clip(m.label.quote, 60)} |")
        add("")

    if result.unlocatable_labels:
        add("### Labelled quotes absent from their own source")
        add("")
        add("These are not pipeline misses — the labelled set and the corpus have drifted apart.")
        add("")
        for label in result.unlocatable_labels:
            add(f"- line {label.line} · {label.source} · “{_clip(label.quote, 90)}”")
        add("")

    return "\n".join(lines).rstrip() + "\n"


def to_payload(result: EvalResult, run_at: str | None = None) -> dict:
    """The same run as JSON. Machine-readable twin of `render_markdown`."""
    from datetime import datetime, timezone

    stamp = run_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    failures = gate_failures(result)
    return {
        "run_at": stamp,
        "gate": {
            "passed": not failures,
            "failures": failures,
            "hard_negative_fp_ceiling": HARD_NEGATIVE_FP_CEILING,
            "stale_material_recall_floor": STALE_MATERIAL_RECALL_FLOOR,
        },
        "run": {
            "sources_read": result.sources_read,
            "verdicts_produced": result.verdicts_produced,
            "labelled_rows": len(result.labels),
            "matched": result.matched,
            "unmatched_labels": len(result.unmatched_labels),
            "unlocatable_labels": len(result.unlocatable_labels),
            "unmatched_verdicts": len(result.unmatched_verdicts),
            "min_label_coverage": result.min_coverage,
            "sensitivity": {f"{t:.2f}": n for t, n in sorted(result.sensitivity.items())},
            "extraction": "cached" if result.cached_extraction else "live",
        },
        "extraction_recall": result.extraction_recall,
        "claim_type": {
            "accuracy": result.claim_type_accuracy,
            "hits": result.claim_type_hits,
            "of": result.matched,
            "confusion": [
                {"expected": e, "got": g, "n": n}
                for (e, g), n in sorted(result.claim_type_confusion().items())
            ],
        },
        "status_scores": [
            {
                "status": s.status,
                "precision": s.precision,
                "recall": s.recall,
                "true_positives": s.true_positives,
                "false_positives": s.false_positives,
                "false_negatives": s.false_negatives,
                "support": s.support,
                "not_extracted": s.not_extracted,
            }
            for s in result.status_scores()
        ],
        "status_confusion": [
            {"expected": e, "got": g, "n": n}
            for (e, g), n in sorted(result.status_confusion().items())
        ],
        "hard_negatives": {
            "labelled": len(result.hard_negative_labels),
            "matched": len(result.hard_negative_matches),
            "false_positives": len(result.hard_negative_false_positives),
            "claim_types": sorted(HARD_NEGATIVE_CLAIM_TYPES),
            "rows": [
                {
                    "line": m.label.line,
                    "source": m.label.source,
                    "quote": m.label.quote,
                    "claim_type": m.label.expected_claim_type,
                    "got": m.verdict.status,
                    "rule_fired": m.verdict.rule_fired,
                }
                for m in result.hard_negative_false_positives
            ],
            "stale_verdicts_outside_labelled_set": len(result.unlabelled_stale_verdicts),
        },
        "misses": [
            {
                "line": m.label.line,
                "source": m.label.source,
                "locator": m.verdict.claim.locator,
                "quote": m.label.quote,
                "coverage": round(m.coverage, 4),
                "iou": round(m.overlap, 4),
                "expected_status": m.label.expected_status,
                "got_status": m.verdict.status,
                "expected_claim_type": m.label.expected_claim_type,
                "got_claim_type": m.verdict.claim.claim_type,
                "expected_entity_key": m.label.expected_entity_key,
                "got_entity_key": m.verdict.claim.entity_key,
                "rule_fired": m.verdict.rule_fired,
                "detail": m.verdict.detail,
                "read": _read_of(m),
            }
            for m in result.misses()
        ],
        "not_extracted": [
            {
                "line": label.line,
                "source": label.source,
                "quote": label.quote,
                "expected_status": label.expected_status,
                "expected_claim_type": label.expected_claim_type,
            }
            for label in result.unmatched_labels
        ],
    }


def write_results(result: EvalResult, out_dir: str | Path | None = None) -> tuple[Path, Path]:
    """Write `results.md` and `results.json`. Never touches `claims.jsonl`."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    directory = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    directory.mkdir(parents=True, exist_ok=True)

    md_path = directory / "results.md"
    json_path = directory / "results.json"
    md_path.write_text(render_markdown(result, now.strftime("%Y-%m-%d %H:%M UTC")), encoding="utf-8")
    json_path.write_text(
        json.dumps(to_payload(result, now.isoformat(timespec="seconds")), indent=2) + "\n",
        encoding="utf-8",
    )
    return md_path, json_path
