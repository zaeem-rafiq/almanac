"""A-01 catalog tests.

Replaces A-00's tests/test_catalog_scaffold.py, whose null-value assertion was the inverse of the
one below: A-00 proved the agent had filled nothing, A-01 proves Zaeem has filled everything.
"""

import os
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from almanac import catalog
from almanac.catalog import MANUAL_MAX_AGE_DAYS, current, freshness_report, load_catalog
from almanac.models import CatalogEntry

REQUIRED_MANUAL = {
    "k401_employee_deferral", "k401_catchup_50plus", "ira_contribution", "ira_catchup_50plus",
    "hsa_self_only", "hsa_family", "standard_deduction_single", "standard_deduction_mfj",
    "ss_wage_base", "gift_annual_exclusion", "ibond_composite_rate",
}
REQUIRED_RATES = {"fed_funds_effective", "mortgage_30y_fixed", "treasury_10y", "cpi_yoy"}


# ------------------------------------------------------------------ shape and completeness

def test_catalog_has_at_least_14_keys():
    assert len(load_catalog()) >= 14


def test_required_keys_present():
    keys = set(load_catalog())
    assert REQUIRED_MANUAL <= keys, f"missing manual: {REQUIRED_MANUAL - keys}"
    assert REQUIRED_RATES <= keys, f"missing rates: {REQUIRED_RATES - keys}"


def test_no_null_values():
    """PROOF A-01 #1. Red until Zaeem fills the manual entries; that is the point."""
    missing = []
    for key in load_catalog():
        fact = current(key)
        if fact.value is None:
            missing.append(key)
    assert not missing, (
        f"{len(missing)} keys have no value: {sorted(missing)}. "
        "Manual values are supplied by Zaeem from irs.gov / ssa.gov / treasurydirect.gov; "
        "rates values come from `python -m almanac rates --refresh`."
    )


def test_manual_entries_have_source_url_and_effective_from():
    incomplete = [
        e.key for e in load_catalog().values()
        if e.source == "manual" and (e.source_url is None or e.effective_from is None)
    ]
    assert not incomplete, f"manual entries missing source_url/effective_from: {sorted(incomplete)}"


def test_manual_entries_carry_history():
    for entry in load_catalog().values():
        if entry.source == "manual":
            assert entry.history, f"{entry.key} has no history block"


def test_unknown_enum_value_fails_at_load():
    with pytest.raises(Exception):
        CatalogEntry(key="x", label="x", kind="not_a_kind", source="manual", unit="usd")


# ------------------------------------------------------------------------------- freshness

def test_stale_manual_fact_is_flagged(monkeypatch):
    """A statutory limit >13 months old with no effective_to is very likely a rolled-over year."""
    old = date.today() - timedelta(days=MANUAL_MAX_AGE_DAYS + 30)
    entry = CatalogEntry(
        key="k401_employee_deferral", label="t", kind="statutory_limit", source="manual",
        unit="usd", value=23500.0, effective_from=old, effective_to=None,
        source_url="https://www.irs.gov/",
    )
    monkeypatch.setattr(catalog, "load_catalog", lambda *a, **k: {entry.key: entry})
    fact = current(entry.key)
    assert fact.stale is True
    assert fact.value == 23500.0, "a stale fact must still carry its value"


def test_manual_fact_with_effective_to_is_not_stale(monkeypatch):
    """A closed window is deliberate history, not neglect."""
    old = date.today() - timedelta(days=MANUAL_MAX_AGE_DAYS + 30)
    entry = CatalogEntry(
        key="ira_contribution", label="t", kind="statutory_limit", source="manual", unit="usd",
        value=7000.0, effective_from=old, effective_to=date.today(),
        source_url="https://www.irs.gov/",
    )
    monkeypatch.setattr(catalog, "load_catalog", lambda *a, **k: {entry.key: entry})
    assert current(entry.key).stale is False


def test_rates_beyond_max_age_warn_but_still_resolve(monkeypatch):
    """PROOF A-01 #3 semantics: staleness ANNOTATES, it never suppresses the verdict."""
    entry = CatalogEntry(
        key="treasury_10y", label="t", kind="market_rate", source="rates", unit="pct",
        max_age_days=5, source_url="https://home.treasury.gov/",
    )
    stale_day = date.today() - timedelta(days=60)
    row = catalog.RateRow(
        value=4.78, as_of=stale_day, fetched_via="treasury",
        fetched_at=datetime.now(timezone.utc), primary_source_url="https://home.treasury.gov/",
    )
    monkeypatch.setattr(catalog, "load_catalog", lambda *a, **k: {entry.key: entry})
    monkeypatch.setattr(catalog, "load_rates", lambda *a, **k: {entry.key: row})
    fact = current(entry.key)
    assert fact.stale is True
    assert fact.value == 4.78, "a stale rate must still return a usable value"
    assert fact.age_days is not None and fact.age_days > 5


def test_freshness_report_covers_every_key():
    report = freshness_report()
    assert {r["key"] for r in report} == set(load_catalog())
    for row in report:
        assert row["note"] is None or "stale feed" in row["note"]


# --------------------------------------------------------------- runtime makes no network call

def test_runtime_reads_rates_json_and_never_calls_the_network(monkeypatch):
    """PROOF A-01 #4: with FMP_API_KEY unset, resolution works and nothing goes out."""
    monkeypatch.delenv("FMP_API_KEY", raising=False)

    def explode(*a, **k):
        raise AssertionError(f"runtime made a network call: {a[:1]}")

    monkeypatch.setattr(requests, "get", explode)
    monkeypatch.setattr(catalog.requests, "get", explode)
    report = freshness_report()
    assert len(report) >= 14
    assert os.environ.get("FMP_API_KEY") is None


def test_no_module_but_catalog_calls_the_rate_feeds():
    """Only catalog.py may reach a rate feed. Grep the package rather than trust convention."""
    from pathlib import Path

    pkg = Path(catalog.__file__).parent
    offenders = []
    for py in pkg.glob("*.py"):
        if py.name in {"catalog.py"}:
            continue
        text = py.read_text()
        if "requests.get" in text or "financialmodelingprep" in text:
            offenders.append(py.name)
    assert not offenders, f"modules other than catalog.py reach the network: {offenders}"


# --------------------------------------------------------------------------- live URL checks

@pytest.mark.network
def test_every_source_url_returns_200():
    """PROOF A-01 #2. Covers manual source_urls and every rates primary_source_url."""
    urls = {}
    for entry in load_catalog().values():
        if entry.source_url:
            urls[entry.key] = entry.source_url
        for h in entry.history:
            if h.source_url:
                urls[f"{entry.key}[history]"] = h.source_url
    assert urls, "no source_url set on any entry yet"
    bad = []
    for key, url in sorted(urls.items()):
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": catalog.USER_AGENT})
            if r.status_code != 200:
                bad.append(f"{key}: {url} -> {r.status_code}")
        except Exception as exc:
            bad.append(f"{key}: {url} -> {type(exc).__name__}")
    assert not bad, "URLs that did not return 200:\n  " + "\n  ".join(bad)
