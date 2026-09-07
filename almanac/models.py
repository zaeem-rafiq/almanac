"""Pydantic models shared across the package.

RULE: protected after A-03 — changes require Zaeem.

`kind`, `source` and `unit` are Literal enums on purpose: a typo in facts/catalog.yaml fails at
load time with a clear error, rather than at verdict time with a wrong answer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["statutory_limit", "tax_bracket", "market_rate"]
Source = Literal["manual", "rates"]
Unit = Literal["usd", "pct"]


class HistoryEntry(BaseModel):
    """A prior-year value and the window it applied to.

    A-03 needs this to answer "was this true when the video was published?", which is a different
    question from "is this true now?".
    """

    value: float | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    source_url: str | None = None


class CatalogEntry(BaseModel):
    """One row of facts/catalog.yaml."""

    key: str
    label: str
    kind: Kind
    source: Source
    unit: Unit
    effective_from: date | None = None
    effective_to: date | None = None
    source_url: str | None = None
    max_age_days: int | None = None
    value: float | None = None
    history: list[HistoryEntry] = Field(default_factory=list)


class RateRow(BaseModel):
    """One entry of facts/rates.json, written only by catalog.refresh_rates()."""

    value: float
    as_of: date
    fetched_via: str
    fetched_at: datetime
    primary_source_url: str
    detail: str | None = None


class Fact(BaseModel):
    """What `current(key)` returns — the single shape judge.py resolves against.

    `stale` annotates; it never suppresses. A stale fact still carries a usable value (see the
    freshness rule in A-01: the report says "as of <date> (stale feed)" and verdicts still run).
    """

    key: str
    label: str
    value: float | None
    unit: Unit
    as_of: date | None
    resolved_from: Source
    source_url: str | None
    stale: bool = False
    max_age_days: int | None = None
    age_days: int | None = None


ClaimType = Literal[
    "statutory_limit",
    "tax_bracket",
    "market_rate",
    "illustrative",
    "historical",
    "other",
]


class Claim(BaseModel):
    """One numeric assertion pulled out of a script or a caption track by extract.py.

    A Claim says what a sentence *is*. It never says whether the sentence is right — that is
    judge.py's job, and every verdict it produces carries a `rule_fired`. Nothing on this model
    is a verdict, and nothing here may become one.

    `source_id` and `locator` are assigned by code, never by the model: they are not in the
    tool schema the model answers, so a hallucinated provenance is not expressible. `quote` is
    the original slice recovered from the source text by offset, so it is verbatim by
    construction rather than by promise.
    """

    source_id: str = Field(description="repo-relative path of the file the claim came from")
    locator: str = Field(description="SRT start timestamp, or 'L<n>' for a script line")
    quote: str = Field(max_length=200, description="verbatim substring of the source text")
    claim_type: ClaimType
    entity_key: str | None = Field(
        default=None, description="a facts/catalog.yaml key, or None when nothing in the catalog fits"
    )
    value: float | None = None
    unit: Unit | None = None
    year_hint: int | None = Field(
        default=None, description="the year the sentence names, when it names one"
    )
    confidence: float = Field(ge=0.0, le=1.0)
