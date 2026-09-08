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


def test_no_null_history_values():
    """The Project Rules say tests fail on null values. `test_no_null_values` only reaches the
    top-level value via `current(key)`, so a scaffolded HISTORY row was invisible to it.

    Found by A-03 finding F-2a: an ibond_composite_rate row for the 2022 window the v5 video
    cites was scaffolded with `value: null`, and the suite stayed green. A history row with no
    value is a fact that judge.py can find but cannot use, which is worse than an absent row —
    it looks like a covering window and answers nothing. Red until Zaeem fills it, exactly as
    `test_no_null_values` is red until the manual entries are filled; that is the point.
    """
    missing = []
    for key, entry in load_catalog().items():
        for i, row in enumerate(entry.history):
            if row.value is None:
                window = f"{row.effective_from} -> {row.effective_to}"
                missing.append(f"{key}[history][{i}] ({window})")
    assert not missing, (
        f"{len(missing)} history row(s) have no value: {missing}. "
        "Manual values are supplied by Zaeem from irs.gov / ssa.gov / treasurydirect.gov; "
        "the agent scaffolds the window and the source_url but never fills the number."
    )


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


def test_today_is_the_local_calendar_date():
    """The catalog holds statutory dates with no timezone; "today" must not come from UTC.

    test_manual_fact_with_effective_to_is_not_stale caught this, but only between UTC midnight and
    local midnight -- it was green for most of the day and red for the rest. This pins the cause
    directly, so the guard does not depend on what time the suite happens to run.
    """
    assert catalog._today() == date.today()


def test_window_closing_today_is_still_open(monkeypatch):
    """The boundary itself: effective_to == today means the last valid day, not the first stale one."""
    day = date(2026, 1, 15)
    entry = CatalogEntry(
        key="ira_contribution", label="t", kind="statutory_limit", source="manual", unit="usd",
        value=7000.0, effective_from=date(2025, 1, 1), effective_to=day,
        source_url="https://www.irs.gov/",
    )
    monkeypatch.setattr(catalog, "load_catalog", lambda *a, **k: {entry.key: entry})
    assert current(entry.key, today=day).stale is False
    assert current(entry.key, today=day + timedelta(days=1)).stale is True


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
    """PROOF A-01 #4: with FMP_API_KEY unset, resolution works and nothing goes out.

    Blocks at the SOCKET layer, not at requests.get — patching one function leaves
    requests.Session, urllib, and httpx wide open, which a review agent proved by smuggling a
    live call past the old version of this test.
    """
    import socket

    monkeypatch.delenv("FMP_API_KEY", raising=False)

    def explode(*a, **k):
        raise AssertionError(f"runtime opened a socket: {a[:1]}")

    monkeypatch.setattr(socket.socket, "connect", explode)
    monkeypatch.setattr(socket, "create_connection", explode)
    monkeypatch.setattr(requests, "get", explode)

    report = freshness_report()
    assert len(report) >= 14

    # ...and prove the rates half actually resolved FROM facts/rates.json, rather than passing
    # vacuously because load_rates returned {} for a missing file.
    resolved = {r["key"]: r for r in report if r["source"] == "rates"}
    assert set(resolved) == REQUIRED_RATES
    assert all(r["value"] is not None and r["as_of"] is not None for r in resolved.values()), (
        f"rates did not resolve from facts/rates.json: {resolved}"
    )


def test_missing_rates_json_is_not_silently_treated_as_success(monkeypatch, tmp_path):
    """The previous version of the test above passed with facts/rates.json deleted."""
    monkeypatch.setattr(catalog, "RATES_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(catalog, "load_rates", lambda *a, **k: {})
    fact = current("treasury_10y")
    assert fact.value is None
    assert fact.stale is True, "an unresolvable rate must be flagged, not pass as fresh"


def test_no_module_but_catalog_calls_the_rate_feeds():
    """Only catalog.py may reach a rate feed. Grep the package rather than trust convention."""
    from pathlib import Path

    pkg = Path(catalog.__file__).parent
    markers = (
        "requests.", "from requests import", "urllib.request", "urlopen", "httpx", "aiohttp",
        "http.client", "socket.socket", "create_connection", "financialmodelingprep",
    )
    offenders = []
    for root in (pkg, pkg.parent / "web"):
        if not root.is_dir():
            continue
        for py in root.rglob("*.py"):
            if py.name == "catalog.py" or "__pycache__" in py.parts:
                continue
            hits = [m for m in markers if m in py.read_text()]
            if hits:
                offenders.append(f"{py.relative_to(pkg.parent)}: {hits}")
    assert not offenders, f"modules other than catalog.py reach the network: {offenders}"


# --------------------------------------------------------------------------- live URL checks

def source_urls_to_check() -> dict[str, list[str]]:
    """Every source_url in the catalog, grouped by URL and labelled by where it came from.

    Grouped by URL rather than keyed by entry. The earlier version keyed this dict by
    f"{entry.key}[history]", which collapsed *all* of a key's history rows onto one slot, so
    only the last row's URL was ever fetched. Once k401_employee_deferral and
    ibond_composite_rate each gained a second history row, that silently skipped two URLs --
    the two most recently added ones, which are exactly the ones a url-200 test exists to
    catch. Labels carry the row index so a second row can never hide behind the first.
    """
    urls: dict[str, list[str]] = {}
    for entry in load_catalog().values():
        if entry.source_url:
            urls.setdefault(entry.source_url, []).append(entry.key)
        for i, h in enumerate(entry.history):
            if h.source_url:
                urls.setdefault(h.source_url, []).append(f"{entry.key}[history][{i}]")
    return urls


def test_url_check_covers_every_history_row():
    """Regression for the collapsed-key bug above -- the PRESENT branch, not the absent one.

    A-01 shipped a wrong verdict because `effective_to` was only exercised when absent. The
    same shape applies here: a catalog where every key has at most one history row cannot
    distinguish the broken collection from the fixed one, so this test asserts against a key
    that actually has two.
    """
    catalog_entries = load_catalog().values()
    expected = sum(
        (1 if e.source_url else 0) + sum(1 for h in e.history if h.source_url)
        for e in catalog_entries
    )
    labels = [label for group in source_urls_to_check().values() for label in group]
    assert len(labels) == len(set(labels)), f"duplicate labels: {labels}"
    assert len(labels) == expected, (
        f"collected {len(labels)} sources but the catalog carries {expected}; "
        "a source_url is escaping the url-200 check"
    )

    multi_row = [e for e in catalog_entries if sum(1 for h in e.history if h.source_url) > 1]
    assert multi_row, (
        "no key has more than one sourced history row, so this regression cannot fail; "
        "the test is only meaningful while such a key exists"
    )
    for entry in multi_row:
        for i in range(len(entry.history)):
            assert f"{entry.key}[history][{i}]" in labels, f"{entry.key} row {i} not checked"


@pytest.mark.network
def test_every_source_url_returns_200():
    """PROOF A-01 #2. Covers manual source_urls, rates primary_source_urls, and every history row."""
    urls = source_urls_to_check()
    assert urls, "no source_url set on any entry yet"
    bad = []
    for url, labels in sorted(urls.items()):
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": catalog.USER_AGENT})
            if r.status_code != 200:
                bad.append(f"{','.join(labels)}: {url} -> {r.status_code}")
        except Exception as exc:
            bad.append(f"{','.join(labels)}: {url} -> {type(exc).__name__}")
    assert not bad, "URLs that did not return 200:\n  " + "\n  ".join(bad)


# ------------------------------------------------------- regressions from the A-01 code review

def test_expired_manual_window_is_flagged_not_served_as_current(monkeypatch):
    """A window that closed in the past must not resolve as the live fact.

    Found by review: `effective_to` was exempting an entry from the age check instead of
    expiring it, so a 2025 limit read as the current 2026 one with stale=False.
    """
    entry = CatalogEntry(
        key="k401_employee_deferral", label="t", kind="statutory_limit", source="manual",
        unit="usd", value=23500.0, effective_from=date(2025, 1, 1),
        effective_to=date(2025, 12, 31), source_url="https://www.irs.gov/",
    )
    monkeypatch.setattr(catalog, "load_catalog", lambda *a, **k: {entry.key: entry})
    fact = current(entry.key, today=date(2026, 9, 7))
    assert fact.stale is True, "a window closed in 2025 must not read as the 2026 fact"
    assert fact.value == 23500.0, "rule 4: annotate, never suppress"


def test_open_window_with_future_effective_to_is_not_stale(monkeypatch):
    entry = CatalogEntry(
        key="ira_contribution", label="t", kind="statutory_limit", source="manual", unit="usd",
        value=7500.0, effective_from=date(2026, 1, 1), effective_to=date(2026, 12, 31),
        source_url="https://www.irs.gov/",
    )
    monkeypatch.setattr(catalog, "load_catalog", lambda *a, **k: {entry.key: entry})
    assert current(entry.key, today=date(2026, 9, 7)).stale is False


def test_cpi_yoy_ignores_bls_annual_average_row(monkeypatch):
    """M13 is BLS's annual average. It sorts after M12 as a string and has no calendar month."""
    payload = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {"series": [{"data": [
            {"year": "2026", "period": "M13", "periodName": "Annual", "value": "330.0"},
            {"year": "2026", "period": "M12", "periodName": "December", "value": "336.0"},
            {"year": "2025", "period": "M13", "periodName": "Annual", "value": "320.0"},
            {"year": "2025", "period": "M12", "periodName": "December", "value": "328.0"},
        ]}]},
    }

    class FakeResponse:
        def json(self):
            return payload

    monkeypatch.setattr(catalog, "_get", lambda *a, **k: FakeResponse())
    value, as_of, via, detail = catalog._fetch_cpi_yoy()
    assert as_of == date(2026, 12, 1), "must pick December, not the annual average"
    assert value == round((336.0 / 328.0 - 1) * 100, 2)
    assert via == "bls"
    assert "M12" in detail
