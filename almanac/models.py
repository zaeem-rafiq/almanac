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
