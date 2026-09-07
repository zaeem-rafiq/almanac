#!/usr/bin/env python3
"""Transcribe Zaeem's figures from scripts/facts_to_fill.tsv into facts/catalog.yaml.

This is a TRANSCRIPTION tool, not a source. Every value it writes comes from the TSV, which only
Zaeem fills. It invents nothing, substitutes no defaults, and refuses to write a partial row.

Edits are line-targeted so the comments in facts/catalog.yaml survive (a YAML round-trip would
strip them).

    python scripts/fill_facts.py            # validate only, show what would change
    python scripts/fill_facts.py --write    # apply
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TSV = ROOT / "scripts" / "facts_to_fill.tsv"
CATALOG = ROOT / "facts" / "catalog.yaml"
REQUIRED = ["value_2026", "effective_from", "source_url",
            "value_2025", "hist_effective_from", "hist_source_url"]


def _die(msg: str) -> None:
    print(f"ERROR: {msg}")
    raise SystemExit(1)


def read_rows() -> dict[str, dict[str, str]]:
    if not TSV.is_file():
        _die(f"{TSV.relative_to(ROOT)} not found")
    rows = {}
    with TSV.open() as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            key = (row.get("key") or "").strip()
            if not key:
                continue
            rows[key] = {k: (v or "").strip() for k, v in row.items()}
    return rows


def validate(rows: dict[str, dict[str, str]]) -> list[str]:
    problems, ready = [], []
    for key, row in rows.items():
        blanks = [c for c in REQUIRED if not row.get(c)]
        if len(blanks) == len(REQUIRED):
            problems.append(f"{key}: not filled in yet")
            continue
        if blanks:
            problems.append(f"{key}: partially filled — missing {blanks}")
            continue
        for col in ("value_2026", "value_2025"):
            try:
                float(row[col].replace(",", "").replace("$", "").replace("%", ""))
            except ValueError:
                problems.append(f"{key}: {col}={row[col]!r} is not a number")
        for col in ("effective_from", "hist_effective_from", "hist_effective_to"):
            if row.get(col):
                try:
                    date.fromisoformat(row[col])
                except ValueError:
                    problems.append(f"{key}: {col}={row[col]!r} is not YYYY-MM-DD")
        for col in ("source_url", "hist_source_url"):
            url = row.get(col, "")
            if url and not url.startswith("https://"):
                problems.append(f"{key}: {col} must be an https:// URL")
        if not any(p.startswith(f"{key}:") for p in problems):
            ready.append(key)
    print(f"{len(ready)} of {len(rows)} rows ready: {sorted(ready) or 'none'}")
    return problems


def _num(raw: str) -> str:
    return str(float(raw.replace(",", "").replace("$", "").replace("%", "")))


def apply(rows: dict[str, dict[str, str]]) -> int:
    lines = CATALOG.read_text().split("\n")
    out, i, changed = [], 0, 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if not line.startswith("  - key: "):
            i += 1
            continue
        key = line.split("  - key: ", 1)[1].strip()
        row = rows.get(key)
        if not row or not row.get("value_2026"):
            i += 1
            continue
        i += 1
        in_history = False
        while i < len(lines) and not lines[i].startswith("  - key: "):
            cur = lines[i]
            stripped = cur.strip()
            if stripped == "history:":
                in_history = True
                out.append(cur)
            elif not in_history and stripped.startswith("value:"):
                out.append(f"    value: {_num(row['value_2026'])}")
            elif not in_history and stripped.startswith("effective_from:"):
                out.append(f"    effective_from: {row['effective_from']}")
            elif not in_history and stripped.startswith("source_url:"):
                out.append(f"    source_url: {row['source_url']}")
            elif in_history and stripped.startswith("- value:"):
                out.append(f"      - value: {_num(row['value_2025'])}")
            elif in_history and stripped.startswith("effective_from:"):
                out.append(f"        effective_from: {row['hist_effective_from']}")
            elif in_history and stripped.startswith("effective_to:"):
                out.append(f"        effective_to: {row.get('hist_effective_to') or 'null'}")
            elif in_history and stripped.startswith("source_url:"):
                out.append(f"        source_url: {row['hist_source_url']}")
            else:
                out.append(cur)
            i += 1
        changed += 1
    CATALOG.write_text("\n".join(out))
    return changed


if __name__ == "__main__":
    data = read_rows()
    issues = validate(data)
    for issue in issues:
        print(f"  - {issue}")
    if "--write" not in sys.argv:
        print("\ndry run — pass --write to apply")
        raise SystemExit(0)
    if issues:
        _die("refusing to write while rows are incomplete or malformed")
    print(f"\nwrote {apply(data)} entries to facts/catalog.yaml")
