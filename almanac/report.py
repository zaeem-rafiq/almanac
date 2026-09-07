"""Renders reports/latest.json and reports/<date>.md from judged claims.

The report shapes live HERE, not in `almanac/models.py`. `models.py` is locked after A-03 and stays
locked: a report row is a presentation of a decision, not a new thing the extractor may assert.
`judge.Verdict` likewise stays exactly as judge.py defines it — a frozen dataclass with no note
field, which is the right shape, because a note is not part of a decision. This module performs the
join.

Every row carries its `rule_fired`. A report a reader has to trust is a worse report than one they
can check, so the rule is a column, not a footnote. Nothing in here decides anything.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import get_args

from pydantic import BaseModel, Field

from almanac.judge import Status, Verdict

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "reports"

STATUS_ORDER = ["stale_material", "stale_immaterial", "correct", "unresolved", "skip"]
STATUS_MARK = {
    "stale_material": "🔴",
    "stale_immaterial": "🟡",
    "correct": "🟢",
    "unresolved": "⚪",
    "skip": "·",
}


class VerdictRow(BaseModel):
    """One judged claim, flattened so a row is auditable without a join back to the extraction."""

    locator: str
    quote: str
    claim_type: str
    entity_key: str | None = None
    claimed_value: float | None = None
    unit: str | None = None
    status: Status
    rule_fired: str
    detail: str
    current_value: float | None = None
    current_effective_from: date | None = None
    delta: float | None = None
    source_url: str | None = None
    fact_stale: bool = False
    note: str | None = None
    note_source: str | None = None


class VideoReport(BaseModel):
    source_id: str
    title: str | None = None
    published_at: datetime | None = None
    verdicts: list[VerdictRow] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class Report(BaseModel):
    run_at: datetime
    source: str
    videos: list[VideoReport] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


def counts_by_status(rows) -> dict[str, int]:
    """Counts for every status, including the zeroes — an absent key hides a zero."""
    tally = {status: 0 for status in get_args(Status)}
    for row in rows:
        tally[row.status] += 1
    return tally


def to_row(verdict: Verdict, notes: dict | None = None) -> VerdictRow:
    """Flatten one judge.Verdict, joining in its note if notes.py drafted one."""
    note, note_source = (notes or {}).get(
        (verdict.claim.source_id, verdict.claim.locator), (None, None)
    )
    fact = verdict.fact
    return VerdictRow(
        locator=verdict.claim.locator,
        quote=verdict.claim.quote,
        claim_type=verdict.claim.claim_type,
        entity_key=verdict.claim.entity_key,
        claimed_value=verdict.claim.value,
        unit=verdict.claim.unit or (fact.unit if fact else None),
        status=verdict.status,
        rule_fired=verdict.rule_fired,
        detail=verdict.detail,
        current_value=verdict.expected,
        current_effective_from=fact.as_of if fact else None,
        delta=None if verdict.delta is None else round(verdict.delta, 6),
        source_url=fact.source_url if fact else None,
        # Annotates, never suppresses (A-01's freshness rule): a stale feed still produced this
        # verdict, and the report says so beside the value rather than dropping the row.
        fact_stale=bool(fact.stale) if fact else False,
        note=note,
        note_source=note_source,
    )


def build_report(source: str, by_source: dict, notes: dict | None = None, run_at=None) -> Report:
    """Assemble the run's verdicts into the shape written to disk."""
    videos = []
    for source_id, verdicts in sorted(by_source.items()):
        rows = [to_row(v, notes) for v in verdicts]
        videos.append(VideoReport(
            source_id=source_id,
            title=_meta(source_id).get("title"),
            published_at=_published_at(source_id),
            verdicts=rows,
            counts=counts_by_status(rows),
        ))
    every = [r for video in videos for r in video.verdicts]
    return Report(
        run_at=run_at or datetime.now(timezone.utc),
        source=source,
        videos=videos,
        counts=counts_by_status(every),
    )


def write_report(report: Report, out_dir: str | Path | None = None) -> tuple[Path, Path]:
    """Write `latest.json` and its Markdown twin, plus dated copies. Returns both latest paths."""
    directory = Path(out_dir).resolve() if out_dir else DEFAULT_OUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.run_at.strftime("%Y-%m-%d")

    payload = report.model_dump_json(indent=2) + "\n"
    markdown = render_markdown(report)

    json_path = directory / "latest.json"
    md_path = directory / "latest.md"
    json_path.write_text(payload, encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    (directory / f"{stamp}.json").write_text(payload, encoding="utf-8")
    (directory / f"{stamp}.md").write_text(markdown, encoding="utf-8")
    return json_path, md_path


def render_markdown(report: Report) -> str:
    """The Markdown twin: same object, same numbers, arranged for a human."""
    stamp = report.run_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Almanac — back-catalogue scan",
        "",
        f"Run {stamp} · source `{report.source}` · {len(report.videos)} source(s) · "
        f"{sum(report.counts.values())} claim(s) judged.",
        "",
        "| status | count |",
        "|---|---|",
    ]
    for status in STATUS_ORDER:
        lines.append(f"| {STATUS_MARK[status]} {status} | {report.counts.get(status, 0)} |")
    lines += [
        "",
        "Every row below carries the rule that produced it. No status in this file came from a "
        "model; `judge.py` decided all of them from `facts/catalog.yaml`.",
    ]

    for video in report.videos:
        lines += ["", f"## {video.title or video.source_id}", "", f"`{video.source_id}`", ""]
        actionable = [r for r in video.verdicts if r.status != "skip"]
        if not actionable:
            lines.append(
                f"No checkable claims — all {len(video.verdicts)} numeric sentence(s) were "
                "skipped by a rule."
            )
            continue
        lines += [
            "| | locator | quote | said | current | Δ | rule |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in actionable:
            lines.append(
                f"| {STATUS_MARK[r.status]} | `{r.locator}` | {_cell(r.quote)} | "
                f"{_n(r.claimed_value)} | {_n(r.current_value)}"
                f"{' ⚠ stale feed' if r.fact_stale else ''} | "
                f"{_delta(r.delta)} | {_cell(r.rule_fired)} |"
            )
        noted = [r for r in video.verdicts if r.note]
        if noted:
            lines += ["", "**Draft update notes**", ""]
            for r in noted:
                suffix = (" _(fallback — the model's phrasing was rejected)_"
                          if r.note_source == "fallback" else "")
                lines.append(f"- {r.note}{suffix}")
        skipped = len(video.verdicts) - len(actionable)
        if skipped:
            lines += ["", f"_{skipped} sentence(s) skipped by rule._"]

    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _n(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _delta(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+,.2f}".rstrip("0").rstrip(".") if value % 1 else f"{value:+,.0f}"


def _meta(source_id: str) -> dict:
    candidate = (REPO_ROOT / source_id).parent / "meta.json"
    if not candidate.is_file():
        return {}
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _published_at(source_id: str) -> datetime | None:
    raw = _meta(source_id).get("published_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
