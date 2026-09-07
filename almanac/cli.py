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


def _cmd_extract(args) -> int:
    """`extract <path>` — label every numeric sentence in a video directory, file, or tree.

    This is one of only two commands that reach a model. It prints labels, never verdicts:
    nothing in this output says whether a number is right. That is `judge` (A-04).
    """
    from almanac.extract import discover_sources, extract_sources

    sources = discover_sources(args.target)
    by_source = extract_sources(sources)

    total = 0
    for source in sources:
        claims = by_source[source.source_id]
        total += len(claims)
        print(f"\n{source.source_id}  —  {len(claims)} claim(s)")
        print(f"{'LOCATOR':<14} {'TYPE':<16} {'ENTITY_KEY':<26} {'VALUE':>12} {'UNIT':<5} {'YR':<5} {'CONF':>5}  QUOTE")
        print("-" * 150)
        for claim in claims:
            value = "—" if claim.value is None else f"{claim.value:,.2f}"
            quote = claim.quote if len(claim.quote) <= 62 else claim.quote[:59] + "..."
            print(
                f"{claim.locator:<14} {claim.claim_type:<16} {(claim.entity_key or '—'):<26} "
                f"{value:>12} {(claim.unit or '—'):<5} {(str(claim.year_hint) if claim.year_hint else '—'):<5} "
                f"{claim.confidence:>5.2f}  {quote}"
            )
    print("-" * 150)
    print(f"{len(sources)} source(s) · {total} claim(s)")
    return 0


def _cmd_judge(args) -> int:
    """`judge <path>` — extract, then decide. Every row shows the rule that decided it.

    The RULE column is the point: a verdict nobody can trace to a rule is a bug, not a default.
    """
    from almanac.extract import discover_sources, extract_sources
    from almanac.judge import judge_all

    sources = discover_sources(args.target)
    by_source = extract_sources(sources)

    counts: dict[str, int] = {}
    for source in sources:
        verdicts = judge_all(by_source[source.source_id])
        shown = [v for v in verdicts if args.all or v.status != "skip"]
        print(f"\n{source.source_id}  —  {len(verdicts)} claim(s), {len(shown)} shown")
        print(f"{'LOCATOR':<14} {'STATUS':<17} {'RULE':<34} {'CLAIMED':>11} {'CURRENT':>11}  DETAIL")
        print("-" * 160)
        for verdict in shown:
            counts[verdict.status] = counts.get(verdict.status, 0) + 1
            claimed = "—" if verdict.claim.value is None else f"{verdict.claim.value:,.2f}"
            expected = "—" if verdict.expected is None else f"{verdict.expected:,.2f}"
            print(f"{verdict.claim.locator:<14} {verdict.status:<17} {verdict.rule_fired:<34} "
                  f"{claimed:>11} {expected:>11}  {verdict.detail[:60]}")
        for verdict in verdicts:
            if verdict.status == "skip" and not args.all:
                counts["skip"] = counts.get("skip", 0) + 1
    print("-" * 160)
    print(" · ".join(f"{status}={n}" for status, n in sorted(counts.items())))
    return 0


CORPUS_CHANNEL = REPO_ROOT / "corpus" / "channel"

STATUS_MARK = {
    "stale_material": "🔴", "stale_immaterial": "🟡", "correct": "🟢",
    "unresolved": "⚪", "skip": "·",
}


def _shown(path):
    """Print a repo-relative path when the output landed inside the repo, else the full one."""
    from pathlib import Path

    try:
        return Path(path).resolve().relative_to(REPO_ROOT)
    except ValueError:
        return Path(path).resolve()


def _judged(sources, draft_notes: bool):
    """extract -> judge -> (optionally) notes. The only order this pipeline may run in.

    The model labels (extract), then the code decides (judge), then the model phrases what the code
    already decided (notes). Nothing downstream of `judge` can change a status: `notes.annotate`
    returns prose keyed by (source_id, locator) and never a verdict.
    """
    from almanac.extract import extract_sources
    from almanac.judge import judge_all
    from almanac.notes import annotate

    by_source = extract_sources(sources)
    judged = {sid: judge_all(claims) for sid, claims in by_source.items()}
    notes = {}
    if draft_notes:
        for verdicts in judged.values():
            notes.update(annotate(verdicts))
    return judged, notes


def _print_rows(source_id: str, rows) -> None:
    print(f"\n{source_id}  —  {len(rows)} claim(s)")
    print(f"{'':<2} {'LOCATOR':<14} {'STATUS':<17} {'SAID':>12} {'CURRENT':>12}  RULE")
    print("-" * 130)
    for r in rows:
        said = "—" if r.claimed_value is None else f"{r.claimed_value:,.2f}"
        now = "—" if r.current_value is None else f"{r.current_value:,.2f}"
        flag = " ⚠stale-feed" if r.fact_stale else ""
        print(f"{STATUS_MARK[r.status]:<2} {r.locator:<14} {r.status:<17} {said:>12} {now:>12}  "
              f"{r.rule_fired}{flag}")
        if r.note:
            print(f"{'':<2} {'':<14} note ({r.note_source}): {r.note}")


def _summary(counts) -> str:
    return "  ".join(
        f"{STATUS_MARK[s]} {s}={counts.get(s, 0)}"
        for s in ["stale_material", "stale_immaterial", "correct", "unresolved", "skip"]
    )


def _cmd_scan(args) -> int:
    """`scan --source corpus|youtube --out reports/` — judge a whole back catalogue.

    Every verdict in this output came from `almanac/judge.py`. Each line carries the rule that
    produced it; a status without a rule would be a bug, not a formatting slip.
    """
    from almanac.extract import discover_sources
    from almanac.report import build_report, write_report

    if args.source == "corpus":
        sources = discover_sources(args.target or CORPUS_CHANNEL)
    else:
        try:
            from almanac import youtube
        except ImportError as exc:  # pragma: no cover - depends on A-06 landing
            print(f"--source youtube needs almanac/youtube.py (A-06): {exc}")
            return 2
        fetch = getattr(youtube, "fetch_sources", None) or getattr(youtube, "iter_sources", None)
        if fetch is None:
            print(
                "--source youtube needs almanac/youtube.py to expose fetch_sources() returning "
                "extract.Source objects; A-06 owns that module and it has not landed on main yet."
            )
            return 2
        sources = list(fetch())

    judged, notes = _judged(sources, draft_notes=not args.no_notes)
    report = build_report(args.source, judged, notes)
    json_path, md_path = write_report(report, args.out)

    for video in report.videos:
        _print_rows(video.source_id, video.verdicts)
    print("-" * 130)
    print(_summary(report.counts))
    print(f"wrote {_shown(json_path)} and {_shown(md_path)}")
    return 0


def _cmd_lint(args) -> int:
    """`lint <path>` — judge one script or caption file and print the verdicts."""
    from almanac.extract import read_source
    from almanac.report import counts_by_status, to_row

    source = read_source(args.target)
    judged, notes = _judged([source], draft_notes=args.notes)
    rows = [to_row(v, notes) for v in judged[source.source_id]]
    _print_rows(source.source_id, rows)
    print("-" * 130)
    print(_summary(counts_by_status(rows)))
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="almanac", description="Almanac — checks the numbers.")
    sub = parser.add_subparsers(dest="command", required=True)

    facts = sub.add_parser("facts", help="print every fact with its value, date and source")
    facts.set_defaults(func=_cmd_facts)

    rates = sub.add_parser("rates", help="refresh facts/rates.json from the primary feeds")
    rates.add_argument("--refresh", action="store_true", help="fetch and write the rates table")
    rates.set_defaults(func=_cmd_rates)

    extract = sub.add_parser("extract", help="label the numeric claims in a script or caption file")
    extract.add_argument("target", help="a .srt/.md file, a video directory, or a tree of them")
    extract.set_defaults(func=_cmd_extract)

    judge_cmd = sub.add_parser("judge", help="extract then decide, showing the rule behind each verdict")
    judge_cmd.add_argument("target", help="a .srt/.md file, a video directory, or a tree of them")
    judge_cmd.add_argument("--all", action="store_true", help="include skipped claims")
    judge_cmd.set_defaults(func=_cmd_judge)

    scan = sub.add_parser("scan", help="judge a whole back catalogue and write a report")
    scan.add_argument("--source", choices=["corpus", "youtube"], default="corpus")
    scan.add_argument("--out", default=None, help="output directory (default: reports/)")
    scan.add_argument("--target", default=None, help="override the corpus directory to scan")
    scan.add_argument("--no-notes", action="store_true", help="skip drafting update notes")
    scan.set_defaults(func=_cmd_scan)

    lint = sub.add_parser("lint", help="judge one script or caption file")
    lint.add_argument("target", help="a .srt/.md file")
    lint.add_argument("--notes", action="store_true", help="also draft the update notes")
    lint.set_defaults(func=_cmd_lint)

    args = parser.parse_args(argv)
    return args.func(args)
