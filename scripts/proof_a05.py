"""A-05 proof harness. Prints each PROOF line and appends it to docs/proofs/A-05.md.

Every proof asserts the SIZE of what it checked, not merely that a check passed. A-01 shipped a
url-200 test that silently dropped 2 of 29 URLs while reporting PASS; a hard-negative gate that
reports "0 false positives" over 0 matched rows is the same failure wearing a different hat.
Each line therefore carries its denominators, and the negative proofs carry a control showing the
check is capable of failing.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from almanac.evaluate import (  # noqa: E402
    HARD_NEGATIVE_FP_CEILING,
    STALE_MATERIAL_RECALL_FLOOR,
    gate_failures,
    run,
    write_results,
)

PROOFS_PATH = REPO_ROOT / "docs" / "proofs" / "A-05.md"
ADR_PATH = REPO_ROOT / "docs" / "decisions" / "ADR-002-eval-loop.md"
JUDGE_TESTS = REPO_ROOT / "tests" / "test_judge.py"
RULES = ("R1", "R2", "R3", "R4", "R5")


def line(text: str, ok: bool, evidence: str) -> str:
    return f"PROOF A-05: {text} = {'PASS' if ok else 'FAIL'}  [{evidence}]"


# ------------------------------------------------------------------------------------ proof 1


def proof_eval_gate() -> tuple[str, bool, object]:
    """Run the real thing end to end and assert the gate, with liveness checked FIRST."""
    started = time.time()
    result = run()
    md_path, json_path = write_results(result)
    failures = gate_failures(result)
    elapsed = time.time() - started

    stale = result.status_score("stale_material")
    recall = stale.recall
    false_positives = len(result.hard_negative_false_positives)
    matched_hard = len(result.hard_negative_matches)

    # Liveness before the negative. "0 false positives" is what an empty run reports.
    alive = (
        len(result.sources_read) > 0
        and result.verdicts_produced > 0
        and result.matched > 0
        and matched_hard > 0
    )
    ok = (
        alive
        and not failures
        and false_positives <= HARD_NEGATIVE_FP_CEILING
        and recall is not None
        and recall >= STALE_MATERIAL_RECALL_FLOOR
        and md_path.is_file()
        and md_path.stat().st_size > 0
        and json_path.is_file()
    )
    evidence = (
        f"{len(result.sources_read)} source(s) → {result.verdicts_produced} verdict(s); "
        f"matched {result.matched}/{len(result.labels)} labelled row(s) "
        f"({result.extraction_recall:.0%} extraction recall) | "
        f"hard-negative false positives {false_positives} over {matched_hard} MATCHED "
        f"illustrative/historical row(s) of {len(result.hard_negative_labels)} labelled — the "
        f"denominator is non-zero, so the negative is not vacuous | "
        f"stale_material recall {recall:.2f} ({stale.true_positives}/{stale.support}) "
        f">= {STALE_MATERIAL_RECALL_FLOOR:.2f}, precision "
        f"{stale.precision:.2f} | status disagreements on matched rows {len(result.misses())} | "
        f"claim_type accuracy {result.claim_type_accuracy:.0%} "
        f"({result.claim_type_hits}/{result.matched}) | "
        f"{md_path.name} {md_path.stat().st_size} bytes, {json_path.name} written | "
        f"gate failures {len(failures)} | {elapsed:.1f}s"
    )
    return line(
        "eval exits 0; hard-negative false positives = 0; stale_material recall >= 0.90; "
        "table written to evals/results.md",
        ok, evidence,
    ), ok, result


def proof_exit_code() -> tuple[str, bool]:
    """The CLI's exit code is the gate. Asserted by running it, not by reading the function."""
    # --out a scratch directory: this proof exercises the exit code, and must not overwrite the
    # evals/results.* that proof 1 just wrote from its own run. They would then be a table from a
    # different run than the numbers the proof line quotes.
    scratch = REPO_ROOT / "evals" / ".cache" / "exit-code-proof"
    completed = subprocess.run(
        [sys.executable, "-m", "almanac", "eval", "--out", str(scratch), "--cache",
         str(REPO_ROOT / "evals" / ".cache" / "extraction.json")],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    ok = completed.returncode == 0 and "GATE PASS" in completed.stdout
    # Control: the same command with an impossible floor must exit non-zero, proving the exit
    # code tracks the gate rather than always being 0.
    control = subprocess.run(
        [sys.executable, "-m", "almanac", "eval", "--min-coverage", "1.01", "--out",
         str(scratch / "control")],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    ok = ok and control.returncode != 0
    return line(
        "`python -m almanac eval` exit code tracks the gate",
        ok,
        f"real run rc={completed.returncode} (GATE PASS printed: "
        f"{'GATE PASS' in completed.stdout}) | control run with an unsatisfiable "
        f"--min-coverage=1.01 rc={control.returncode} — the check can fail",
    ), ok


# ------------------------------------------------------------------------------------ proof 2


def proof_adr_review() -> tuple[str, bool]:
    """ADR-002 must record at least one miss Zaeem reviewed, with what was changed.

    This proof cannot be satisfied by the agent alone, and that is the point of the milestone:
    a miss is resolved by a human deciding whether the LABEL or the SYSTEM was wrong. It reads
    RATIFIED entries only — a row still marked AWAITING RATIFICATION does not count.
    """
    if not ADR_PATH.is_file():
        return line("ADR-002 lists >=1 miss reviewed by Zaeem with the fix applied "
                    "(label | prompt | rule)", False, "docs/decisions/ADR-002-eval-loop.md absent"), False
    text = ADR_PATH.read_text(encoding="utf-8")
    ratified = text.count("**Status:** RATIFIED")
    awaiting = text.count("**Status:** AWAITING RATIFICATION")
    has_axis = any(f"**Fix applied:** {axis}" in text for axis in ("label", "prompt", "rule", "harness"))
    ok = ratified >= 1 and has_axis
    return line(
        "ADR-002 lists >=1 miss reviewed by Zaeem with the fix applied (label | prompt | rule)",
        ok,
        f"{ratified} decision(s) marked RATIFIED, {awaiting} AWAITING RATIFICATION; "
        f"a 'Fix applied:' axis present: {has_axis}"
        + ("" if ok else " — Zaeem has not signed off a miss yet; see the review table in ADR-002"),
    ), ok


# ------------------------------------------------------------------------------------ proof 3


def proof_judge_tests() -> tuple[str, bool]:
    """Every rule R1-R5 has at least one dedicated test carrying at least one assertion.

    Counted from the AST rather than by grepping names, so a test that asserts nothing cannot
    satisfy the proof by being named correctly.
    """
    tree = ast.parse(JUDGE_TESTS.read_text(encoding="utf-8"))
    per_rule: dict[str, list[tuple[str, int]]] = {rule: [] for rule in RULES}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        asserts = sum(1 for child in ast.walk(node) if isinstance(child, ast.Assert))
        for rule in RULES:
            if node.name.startswith(f"test_{rule.lower()}_") and asserts > 0:
                per_rule[rule].append((node.name, asserts))

    covered = [rule for rule in RULES if per_rule[rule]]
    # Control: a rule that does not exist must come back uncovered, proving the check discriminates.
    phantom = [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name.startswith("test_r9_")]
    ok = len(covered) == len(RULES) and not phantom

    detail = " | ".join(
        f"{rule}: {len(per_rule[rule])} test(s), {sum(a for _, a in per_rule[rule])} assertion(s)"
        for rule in RULES
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(JUDGE_TESTS), "-q"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    green = completed.returncode == 0
    ok = ok and green
    tail = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "no output"
    # WHICH numbering: judge.py's own, not the issue text's. The two diverge from R2 onward, a
    # deliberate change recorded in docs/walkthroughs/A-04.md. Asserting the issue's numbering
    # would check nothing — a statutory limit that differs fires R5.stale_material here, not R4.
    # So the proof also confirms each asserted rule name is a prefix judge.py actually emits.
    judge_src = (REPO_ROOT / "almanac" / "judge.py").read_text(encoding="utf-8")
    # R5 builds its name with an f-string (`rule_fired=f"R5.{status}"`), so a literal-prefix scan
    # misses it. Match both spellings.
    emitted = sorted(set(re.findall(r'rule_fired=f?"(R\d)\.', judge_src)))
    ok = ok and emitted == list(RULES)
    return line(
        "pytest tests/test_judge.py covers R1-R5 with one assertion each",
        ok,
        f"asserted against judge.py's OWN numbering (R1=historical window, R2=unjudged type, "
        f"R4=exact match, R5=divergence), not the issue text's — the two diverge from R2 onward "
        f"per docs/walkthroughs/A-04.md; rule prefixes judge.py actually emits: "
        f"{','.join(emitted)} | {detail} | control: 0 test_r9_* functions, so the counter "
        f"discriminates | pytest tests/test_judge.py → {tail}",
    ), ok


def main() -> int:
    lines: list[str] = []
    gate_line, gate_ok, result = proof_eval_gate()
    exit_line, exit_ok = proof_exit_code()
    adr_line, adr_ok = proof_adr_review()
    tests_line, tests_ok = proof_judge_tests()

    for text in (gate_line, exit_line, adr_line, tests_line):
        print(text)
        lines.append(text)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"\n## Run {stamp}\n\n"
        f"`venv/bin/python scripts/proof_a05.py` — {result.verdicts_produced} verdict(s) from "
        f"{len(result.sources_read)} source(s), {result.matched}/{len(result.labels)} labelled "
        f"rows matched.\n\n```\n" + "\n".join(lines) + "\n```\n"
    )
    PROOFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROOFS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(header)

    all_ok = gate_ok and exit_ok and adr_ok and tests_ok
    print(f"\n{sum([gate_ok, exit_ok, adr_ok, tests_ok])}/4 proof line(s) PASS")
    if not all_ok:
        print("A-05 is NOT done: a PROOF line is not PASS.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
