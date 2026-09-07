"""A-04 proof harness — runs the real pipeline over the corpus and prints the four PROOF lines.

Prints each line to the transcript and appends the same lines to docs/proofs/A-04.md.

Three of these proofs are shaped by mistakes earlier issues already paid for:

  * Proof 4 is a PURE NEGATIVE assertion ("extract.py contains no verdict vocabulary"). A grep
    that matches nothing and a grep pointed at nothing look identical, and A-01 shipped exactly
    that bug twice. So the harness runs the SAME grep against judge.py first — a file that is
    full of the words — and fails if that probe does not go red. A typo in the pattern or the
    path now fails the proof instead of passing it.

  * Proof 2 asserts a property over "every stale_material verdict". "All 0 of them passed" is the
    failure mode, so the count is printed and asserted non-zero BEFORE the property is checked.

  * Proof 1's V4 clause is negative too ("0 non-skip verdicts"). It also asserts V4 produced
    claims at all, and that non-skip verdicts DID fire elsewhere in the same run.

The report this writes is the artifact `reports/latest.json`; the proofs read it back from disk
rather than from memory, so what is asserted is what shipped.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from almanac.extract import read_source  # noqa: E402
from almanac.judge import MARKET_RATE_MATERIAL_PP  # noqa: E402
from almanac.report import Report, to_row  # noqa: E402
from almanac.notes import format_value  # noqa: E402

PROOF_PATH = REPO_ROOT / "docs/proofs/A-04.md"
REPORT_PATH = REPO_ROOT / "reports/latest.json"

V1 = "corpus/channel/v1-401k-limits-explained/captions.srt"
V4 = "corpus/channel/v4-how-id-invest-10000/captions.srt"
V5 = "corpus/channel/v5-ibonds-vs-high-yield-savings/captions.srt"
FRESH_WRONG = "corpus/scripts/fresh_wrong.md"
FRESH_OK = "corpus/scripts/fresh_ok.md"

# The pattern proof 4 asserts finds nothing in extract.py, and the file it must find plenty in.
VERDICT_VOCABULARY = r"stale\|material\|correct"
PROBE_FILE = "almanac/judge.py"

# The issue text numbers the rules R1..R5 with R1=skip and R4=exact-match. `609af65` renumbered
# them on main: R1 resolves a historical claim against its history window, R2 skips the unjudged
# claim types, and a statutory limit that differs fires R5.stale_material rather than R4. The
# proofs below therefore assert the STATUS the issue specifies — which is stable — and print the
# rule that actually fired, rather than asserting a rule number that no longer means what the
# issue meant by it. Asserting the old numbers against the new judge would be checking nothing.
SKIP_RULES = ("R1", "R2")        # historical-matches-window, and unjudged claim types
DIVERGENCE_RULE = "R5"           # the only rule that can call something stale


def main() -> int:
    started = time.monotonic()

    # --- run the real CLI, so the proofs describe what the command actually does -------------
    scan = subprocess.run(
        [sys.executable, "-m", "almanac", "scan", "--source", "corpus", "--out", "reports/"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if scan.returncode != 0:
        print(scan.stdout[-4000:], scan.stderr[-4000:])
        raise SystemExit("scan failed")
    print(scan.stdout)

    report = Report.model_validate(json.loads(REPORT_PATH.read_text(encoding="utf-8")))
    by_source = {video.source_id: video.verdicts for video in report.videos}
    every = [v for video in report.videos for v in video.verdicts]

    lines = [
        _proof_1(by_source),
        _proof_2(every),
        _proof_3(),
        _proof_4(),
    ]

    wall = time.monotonic() - started
    print()
    for line in lines:
        print(line)
    _append(lines, len(every), wall)
    return 0 if all("= PASS" in line for line in lines) else 1


# ------------------------------------------------------------------------------------ proof 1

def _proof_1(by_source: dict) -> str:
    check = ("scan of 5-video corpus → V1 has stale_material (divergence rule), V4 has 0 non-skip "
             "verdicts (skip rules), V5 has exactly one historical skip and one stale_material")
    if len(by_source) != 5:
        return _line(check, False, f"expected 5 sources, judged {len(by_source)}")

    v1 = [v for v in by_source.get(V1, []) if v.status == "stale_material"]
    v1_r4 = [v for v in v1 if v.rule_fired.startswith(DIVERGENCE_RULE)]

    v4_all = by_source.get(V4, [])
    v4_non_skip = [v for v in v4_all if v.status != "skip"]
    v4_r1 = all(v.rule_fired.startswith(SKIP_RULES) for v in v4_all)
    # The negative clause needs a live control: non-skip verdicts must have fired SOMEWHERE.
    elsewhere = [v for sid, vs in by_source.items() if sid != V4 for v in vs if v.status != "skip"]

    v5 = by_source.get(V5, [])
    v5_hist = [v for v in v5 if v.claim_type == "historical" and v.status == "skip"]
    v5_material = [v for v in v5 if v.status == "stale_material"]

    passed = (
        bool(v1_r4)
        and len(v4_all) > 0 and not v4_non_skip and v4_r1 and bool(elsewhere)
        and len(v5_hist) == 1 and len(v5_material) == 1
    )
    detail = (
        f"V1 stale_material={len(v1)} of which {DIVERGENCE_RULE}*={len(v1_r4)} "
        f"({v1_r4[0].rule_fired if v1_r4 else 'none'}) | "
        f"V4 {len(v4_all)} verdict(s), {len(v4_non_skip)} non-skip, all {SKIP_RULES}={v4_r1}; "
        f"control: {len(elsewhere)} non-skip verdict(s) elsewhere in the same run | "
        f"V5 historical-skip={len(v5_hist)}, stale_material={len(v5_material)} "
        f"({v5_material[0].rule_fired if v5_material else 'none'})"
    )
    return _line(check, passed, detail)


# ------------------------------------------------------------------------------------ proof 2

def _proof_2(every: list) -> str:
    check = ("every stale_material verdict has a note containing the current value string; "
             "every verdict has rule_fired")
    material = [v for v in every if v.status == "stale_material"]

    # Assert the SIZE before the property. "All 0 of them passed" is not a proof of anything.
    if not material:
        return _line(check, False, "0 stale_material verdicts in the run — nothing was checked")

    missing_note, missing_value, checked = [], [], 0
    for v in material:
        checked += 1
        if not v.note:
            missing_note.append(v.locator)
            continue
        wanted = format_value(v.current_value, v.unit)
        if not wanted or wanted not in v.note:
            missing_value.append(f"{v.locator} wanted {wanted!r}")

    no_rule = [v.locator for v in every if not v.rule_fired]
    fallbacks = [v.locator for v in material if v.note_source == "fallback"]

    passed = checked == len(material) and not missing_note and not missing_value and not no_rule
    detail = (
        f"{len(material)} stale_material verdict(s), {checked} checked, "
        f"{len(missing_note)} without a note, {len(missing_value)} whose note omits the value "
        f"({'; '.join(missing_value) or 'none'}) | rule_fired checked {len(every)}/{len(every)}, "
        f"{len(no_rule)} missing | note_source=model on {len(material) - len(fallbacks)}, "
        f"fallback on {len(fallbacks)}"
    )
    return _line(check, passed, detail)


# ------------------------------------------------------------------------------------ proof 3

def _proof_3() -> str:
    check = ("lint fresh_wrong.md → 1 stale_material IRA limit + mortgage verdict per R5 "
             "+ the rest skips; lint fresh_ok.md → 0 stale")
    from almanac.cli import _judged

    judged, _ = _judged(
        [read_source(REPO_ROOT / FRESH_WRONG), read_source(REPO_ROOT / FRESH_OK)],
        draft_notes=False,
    )
    wrong = [to_row(v) for v in judged[FRESH_WRONG]]
    ok = [to_row(v) for v in judged[FRESH_OK]]

    material = [v for v in wrong if v.status == "stale_material"]
    ira = [v for v in material if v.entity_key == "ira_contribution"]
    mortgage = [v for v in wrong if v.entity_key == "mortgage_30y_fixed"]
    skips = [v for v in wrong if v.status == "skip"]
    other = [v for v in wrong if v.status != "skip" and v.entity_key
             not in {"ira_contribution", "mortgage_30y_fixed"}]

    # "per R5" is re-derived here from judge.py's own constant rather than hardcoded, so the proof
    # checks the rubric rather than restating today's answer. main's rubric for a market_rate is a
    # single bar: R4 takes an exact match first, then >= the bar is material and under it is not.
    band_ok, band_says = False, "no mortgage verdict"
    if len(mortgage) == 1 and mortgage[0].delta is not None:
        drift = abs(mortgage[0].delta)
        expected = ("correct" if drift == 0
                    else "stale_material" if drift >= MARKET_RATE_MATERIAL_PP
                    else "stale_immaterial")
        band_ok = mortgage[0].status == expected
        band_says = (f"drift {drift:.2f}pp vs the {MARKET_RATE_MATERIAL_PP:.2f}pp bar → {expected}, "
                     f"verdict says {mortgage[0].status}")

    ok_stale = [v for v in ok if v.status in {"stale_material", "stale_immaterial"}]

    passed = (
        len(material) == 1 and len(ira) == 1
        and band_ok and len(skips) >= 2 and not other
        and not ok_stale and len(ok) > 0
    )
    detail = (
        f"fresh_wrong: {len(wrong)} verdict(s) — stale_material={len(material)} "
        f"(ira_contribution={len(ira)}, {ira[0].rule_fired if ira else 'none'}), "
        f"mortgage per R5: {band_says}, skips={len(skips)}, unaccounted non-skip={len(other)} | "
        f"fresh_ok: {len(ok)} verdict(s), {len(ok_stale)} stale"
    )
    return _line(check, passed, detail)


# ------------------------------------------------------------------------------------ proof 4

def _proof_4() -> str:
    check = f'grep -r "{VERDICT_VOCABULARY}" almanac/extract.py returns nothing'

    # The control FIRST. If this does not go red, the pattern or the invocation is broken and the
    # real check below would pass for the wrong reason.
    probe = _grep(PROBE_FILE)
    target = _grep("almanac/extract.py")

    probe_red = probe.returncode == 0 and probe.stdout.strip()
    target_clean = target.returncode == 1 and not target.stdout.strip()
    extract_exists = (REPO_ROOT / "almanac/extract.py").is_file()

    passed = bool(probe_red) and target_clean and extract_exists
    detail = (
        f"control: the same grep against {PROBE_FILE} returned "
        f"{len(probe.stdout.splitlines())} line(s) (rc={probe.returncode}) — the check can fail | "
        f"target: almanac/extract.py exists={extract_exists}, "
        f"{len(target.stdout.splitlines())} line(s) (rc={target.returncode})"
    )
    return _line(check, passed, detail)


def _grep(relative: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["grep", "-rn", "-i", "-E", "stale|material|correct", relative],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )


# ------------------------------------------------------------------------------------- output

def _line(check: str, passed: bool, detail: str) -> str:
    return f"PROOF A-04: {check} = {'PASS' if passed else 'FAIL'}  [{detail}]"


def _append(lines: list[str], total: int, wall_time: float) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = [
        f"\n## Run {stamp}",
        "",
        f"`venv/bin/python scripts/proof_a04.py` — {total} verdict(s) from 5 videos "
        f"plus 2 scripts in {wall_time:.1f}s.",
        "",
        "```",
        *lines,
        "```",
        "",
    ]
    PROOF_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PROOF_PATH.exists():
        PROOF_PATH.write_text("# A-04 — judge / notes / report · proofs\n", encoding="utf-8")
    with PROOF_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(body))


if __name__ == "__main__":
    raise SystemExit(main())
