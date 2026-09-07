"""Almanac command-line entry point and configuration loading.

RULE (Project Rules, "Secrets"): secrets come only from `.env` locally and from GitHub Actions
secrets / host env in the cloud. Never print a secret; print a prefix check only.

`python-dotenv` is deliberately not used — it is not in the approved package list and new
dependencies need Zaeem's approval (ADR-000 gap G-5). Hence the small reader below.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"


def load_env(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Read `.env` into the process environment and return the effective values.

    Rules:
      * a missing file is not an error — returns whatever is already in `os.environ`
      * blank lines and `#` comments are skipped
      * a line splits on its FIRST `=` only, so values may contain `=`
      * surrounding single or double quotes are stripped
      * a variable already set in `os.environ` is NOT overwritten, so CI secrets and host env
        always win over a file on disk
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV_PATH
    parsed: dict[str, str] = {}

    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            parsed[key] = value

    for key, value in parsed.items():
        os.environ.setdefault(key, value)

    return {key: os.environ[key] for key in parsed if key in os.environ}


def mask(value: str | None, keep: int = 6) -> str:
    """Render a secret safely: a short leading prefix and a length. Never the tail.

    Returns "ABSENT" for a missing or empty value so preflight can name what is unset without
    ever emitting the value itself.
    """
    if not value:
        return "ABSENT"
    if len(value) <= keep:
        return f"…(len {len(value)})"
    return f"{value[:keep]}…(len {len(value)})"


def _cmd_facts(_args) -> int:
    """Print every key with its current value, as-of/effective date, and source.

    Reads facts/catalog.yaml and facts/rates.json only. Makes no network call, and needs no
    FMP_API_KEY — that is the runtime contract A-01 establishes.
    """
    from almanac.catalog import freshness_report

    rows = freshness_report()
    print(f"{'KEY':<28} {'VALUE':>12}  {'AS OF':<12} {'SOURCE':<8} NOTE")
    print("-" * 88)
    missing = 0
    for row in rows:
        value = "—" if row["value"] is None else f"{row['value']:,.2f}"
        if row["value"] is None:
            missing += 1
        as_of = row["as_of"].isoformat() if row["as_of"] else "—"
        note = row["note"] or ""
        print(f"{row['key']:<28} {value:>12}  {as_of:<12} {row['source']:<8} {note}")
    print("-" * 88)
    stale = sum(1 for r in rows if r["stale"])
    print(f"{len(rows)} keys · {missing} without a value · {stale} stale")
    return 0


def _cmd_rates(args) -> int:
    """`rates --refresh` is the only command that reaches the network for rate data."""
    from almanac.catalog import refresh_rates

    if not args.refresh:
        print("nothing to do: pass --refresh")
        return 0
    rows, failures = refresh_rates()
    for key, row in sorted(rows.items()):
        print(f"  {key:<24} {row.value:>10}  as_of={row.as_of}  via={row.fetched_via}")
    for failure in failures:
        print(f"  FAILED {failure}")
    print(f"wrote {len(rows)} keys to facts/rates.json" + (f", {len(failures)} failed" if failures else ""))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="almanac", description="Almanac — checks the numbers.")
    sub = parser.add_subparsers(dest="command", required=True)

    facts = sub.add_parser("facts", help="print every fact with its value, date and source")
    facts.set_defaults(func=_cmd_facts)

    rates = sub.add_parser("rates", help="refresh facts/rates.json from the primary feeds")
    rates.add_argument("--refresh", action="store_true", help="fetch and write the rates table")
    rates.set_defaults(func=_cmd_rates)

    args = parser.parse_args(argv)
    return args.func(args)
