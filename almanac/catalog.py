"""Loads facts/catalog.yaml and facts/rates.json, and refreshes the rates table.

RULE: `refresh_rates()` is the ONLY function in this codebase permitted to reach the network for
rate data. Every other module — extract, judge, notes, report, web — reads facts/rates.json.

RULE (freshness): staleness ANNOTATES, it never suppresses. A stale feed sets `Fact.stale` and is
surfaced as "as of <date> (stale feed)"; the value is still returned and verdicts still run.

Source note (ADR-000 §9): FMP's `economic-indicators` returns a ~9-month-old window on our key and
cannot supply a 12-month-apart CPI pair, so the four keyless primary feeds authorised by HAC-37 §6
are used instead. The runtime contract is unchanged; only `fetched_via` names the real fetcher.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

import requests
import yaml

from almanac.models import CatalogEntry, Fact, RateRow

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "facts" / "catalog.yaml"
RATES_PATH = REPO_ROOT / "facts" / "rates.json"

# A statutory limit whose window opened more than 13 months ago, with no close date, has very
# likely rolled over into a new tax year without anyone updating it. Flag it (annotate, never
# suppress) so a silently-expired IRS figure cannot masquerade as current.
MANUAL_MAX_AGE_DAYS = 395

HTTP_TIMEOUT = 30
USER_AGENT = "almanac/0.1 (+https://github.com/zaeem-rafiq/almanac)"


# --------------------------------------------------------------------------------- read path

def load_catalog(path: Path | None = None) -> dict[str, CatalogEntry]:
    """Parse facts/catalog.yaml into validated entries, keyed by `key`.

    Raises on an unknown kind/source/unit — a typo fails here, not at verdict time.
    """
    raw = yaml.safe_load((path or CATALOG_PATH).read_text(encoding="utf-8"))
    entries = [CatalogEntry(**row) for row in raw["entities"]]
    by_key: dict[str, CatalogEntry] = {}
    for entry in entries:
        if entry.key in by_key:
            raise ValueError(f"duplicate catalog key: {entry.key!r}")
        by_key[entry.key] = entry
    return by_key


def load_rates(path: Path | None = None) -> dict[str, RateRow]:
    """Read facts/rates.json. Never touches the network."""
    p = path or RATES_PATH
    if not p.is_file():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k: RateRow(**v) for k, v in raw.items()}


def _age_days(as_of: date | None, today: date | None = None) -> int | None:
    if as_of is None:
        return None
    return ((today or datetime.now(timezone.utc).date()) - as_of).days


def current(key: str, today: date | None = None) -> Fact:
    """Resolve one key to the Fact judge.py will reason about.

    `source: manual` resolves from the catalog; `source: rates` resolves from facts/rates.json.
    """
    catalog = load_catalog()
    if key not in catalog:
        raise KeyError(f"unknown catalog key: {key!r}")
    entry = catalog[key]

    if entry.source == "manual":
        age = _age_days(entry.effective_from, today)
        stale = (
            entry.effective_to is None and age is not None and age > MANUAL_MAX_AGE_DAYS
        )
        return Fact(
            key=entry.key, label=entry.label, value=entry.value, unit=entry.unit,
            as_of=entry.effective_from, resolved_from="manual", source_url=entry.source_url,
            stale=stale, max_age_days=entry.max_age_days or MANUAL_MAX_AGE_DAYS, age_days=age,
        )

    row = load_rates().get(key)
    if row is None:
        return Fact(
            key=entry.key, label=entry.label, value=None, unit=entry.unit, as_of=None,
            resolved_from="rates", source_url=entry.source_url, stale=True,
            max_age_days=entry.max_age_days, age_days=None,
        )
    age = _age_days(row.as_of, today)
    stale = entry.max_age_days is not None and age is not None and age > entry.max_age_days
    return Fact(
        key=entry.key, label=entry.label, value=row.value, unit=entry.unit, as_of=row.as_of,
        resolved_from="rates", source_url=row.primary_source_url or entry.source_url,
        stale=stale, max_age_days=entry.max_age_days, age_days=age,
    )


def freshness_report(today: date | None = None) -> list[dict]:
    """Per-key freshness. Stale entries are flagged, never withheld."""
    report = []
    for key, entry in load_catalog().items():
        fact = current(key, today=today)
        report.append({
            "key": key, "label": entry.label, "source": entry.source,
            "value": fact.value, "as_of": fact.as_of, "age_days": fact.age_days,
            "max_age_days": fact.max_age_days, "stale": fact.stale,
            "note": f"as of {fact.as_of} (stale feed)" if fact.stale else None,
        })
    return report


# ------------------------------------------------------------------ refresh path (network)

def _get(url: str, **params) -> requests.Response:
    response = requests.get(
        url, params=params or None, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
    )
    response.raise_for_status()
    return response


def _fetch_fed_funds_effective() -> tuple[float, date, str, str]:
    d = _get("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json").json()
    row = d["refRates"][0]
    return float(row["percentRate"]), date.fromisoformat(row["effectiveDate"]), "nyfed", ""


def _fetch_mortgage_30y_fixed() -> tuple[float, date, str, str]:
    text = _get("https://www.freddiemac.com/pmms/docs/PMMS_history.csv").text
    rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0].strip() and r[1].strip()]
    header, data = rows[0], rows[1:]
    newest = data[-1]
    month, day, year = (int(x) for x in newest[0].split("/"))
    return float(newest[1]), date(year, month, day), "freddiemac", f"column={header[1]}"


def _fetch_treasury_10y() -> tuple[float, date, str, str]:
    year = datetime.now(timezone.utc).year
    text = _get(
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml",
        data="daily_treasury_yield_curve", field_tdr_date_value=str(year),
    ).text
    ns = {"d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
    root = ET.fromstring(text)
    pairs = []
    for prop in root.iter():
        if not prop.tag.endswith("}properties"):
            continue
        dt = prop.find("d:NEW_DATE", ns)
        yv = prop.find("d:BC_10YEAR", ns)
        if dt is not None and yv is not None and yv.text:
            pairs.append((date.fromisoformat(dt.text[:10]), float(yv.text)))
    if not pairs:
        raise RuntimeError("no BC_10YEAR rows in Treasury XML")
    as_of, value = max(pairs, key=lambda p: p[0])
    return value, as_of, "treasury", "field=BC_10YEAR"


def _fetch_cpi_yoy() -> tuple[float, date, str, str]:
    """CPI-U year-over-year percent, derived from two BLS observations 12 months apart."""
    d = _get("https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0").json()
    if d.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS status={d.get('status')} {d.get('message')}")
    series = d["Results"]["series"][0]["data"]
    monthly = [r for r in series if r["period"].startswith("M")]
    latest = max(monthly, key=lambda r: (int(r["year"]), r["period"]))
    prior = next(
        (r for r in monthly
         if r["period"] == latest["period"] and int(r["year"]) == int(latest["year"]) - 1),
        None,
    )
    if prior is None:
        raise RuntimeError(f"no {latest['period']} observation for {int(latest['year']) - 1}")
    latest_v, prior_v = float(latest["value"]), float(prior["value"])
    yoy = (latest_v / prior_v - 1.0) * 100.0
    as_of = date(int(latest["year"]), int(latest["period"][1:]), 1)
    detail = (f"CUUR0000SA0 {latest['year']}-{latest['period']}={latest_v} vs "
              f"{prior['year']}-{prior['period']}={prior_v}")
    return round(yoy, 2), as_of, "bls", detail


FETCHERS = {
    "fed_funds_effective": _fetch_fed_funds_effective,
    "mortgage_30y_fixed": _fetch_mortgage_30y_fixed,
    "treasury_10y": _fetch_treasury_10y,
    "cpi_yoy": _fetch_cpi_yoy,
}


def refresh_rates(path: Path | None = None) -> tuple[dict[str, RateRow], list[str]]:
    """Fill facts/rates.json from the primary feeds.

    A fetcher that fails leaves the PREVIOUS entry intact and is reported — a network blip must
    never blank a good value.
    """
    out_path = path or RATES_PATH
    catalog = load_catalog()
    existing = load_rates(out_path)
    rows: dict[str, RateRow] = dict(existing)
    failures: list[str] = []

    for key, fetch in FETCHERS.items():
        try:
            value, as_of, via, detail = fetch()
        except Exception as exc:
            kept = "kept previous value" if key in existing else "NO previous value"
            failures.append(f"{key}: {type(exc).__name__}: {str(exc)[:80]} ({kept})")
            continue
        rows[key] = RateRow(
            value=value, as_of=as_of, fetched_via=via,
            fetched_at=datetime.now(timezone.utc),
            primary_source_url=catalog[key].source_url or "",
            detail=detail or None,
        )

    out_path.write_text(
        json.dumps({k: json.loads(v.model_dump_json()) for k, v in sorted(rows.items())}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return rows, failures
