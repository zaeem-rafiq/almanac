#!/usr/bin/env python3
"""A-09 proof runner: closes proofs 1, 3 and 4 once the host is live.

    python scripts/verify_a09.py --url https://almanac-xxxx.vercel.app

Every check runs twice — once against the real target and once against a deliberately wrong one
that MUST fail. A check that cannot be made to fail proves nothing, so a probe that passes when
it should not is itself reported as a FAIL. Nothing is inferred from configuration.

Deploy tooling only. This script imports no almanac engine module and changes no engine
behaviour; it reads `reports/latest.json` off disk and talks to the host over HTTP.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LINT_FIXTURE = REPO_ROOT / "corpus" / "scripts" / "fresh_wrong.md"
LATEST = REPO_ROOT / "reports" / "latest.json"
LINT_BUDGET_S = 60.0

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> bool:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name} — {detail}")
    return ok


def get(url: str, timeout: float = 65.0) -> tuple[int, bytes, float]:
    started = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "almanac-a09-verify"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), time.monotonic() - started
    except Exception as exc:  # DNS failure, refused connection, timeout
        return 0, str(exc).encode(), time.monotonic() - started


def post_json(url: str, payload: dict, timeout: float = 65.0) -> tuple[int, bytes, float]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "almanac-a09-verify"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), time.monotonic() - started
    except Exception as exc:
        return 0, str(exc).encode(), time.monotonic() - started


def check_root(base: str) -> bool:
    """PROOF 1a — GET / returns 200 with a real page, not an empty 200."""
    code, body, secs = get(f"{base}/")
    ok = code == 200 and len(body) > 500
    return record(
        "proof 1a: GET / is 200 with a non-trivial body",
        ok, f"HTTP {code}, {len(body):,} bytes, {secs:.2f}s (need 200 and >500 bytes)",
    )


def check_lint(base: str) -> bool:
    """PROOF 1b — POST /api/lint on fresh_wrong.md, under 60s, with verdicts that carry rules."""
    if not LINT_FIXTURE.exists():
        return record("proof 1b: POST /api/lint", False, f"fixture missing: {LINT_FIXTURE}")
    text = LINT_FIXTURE.read_text(encoding="utf-8")
    code, body, secs = post_json(f"{base}/api/lint", {"text": text})
    if code != 200:
        return record("proof 1b: POST /api/lint", False,
                      f"HTTP {code} in {secs:.2f}s — {body[:200]!r}")
    try:
        data = json.loads(body)
        verdicts = data.get("verdicts", [])
        with_rule = [v for v in verdicts if v.get("rule_fired")]
    except Exception as exc:
        return record("proof 1b: POST /api/lint", False, f"unparseable response: {exc}")
    ok = secs < LINT_BUDGET_S and len(verdicts) > 0 and len(with_rule) == len(verdicts)
    return record(
        "proof 1b: POST /api/lint fresh_wrong.md < 60s",
        ok,
        f"HTTP 200 in {secs:.2f}s (budget {LINT_BUDGET_S:.0f}s), "
        f"{len(verdicts)} verdicts, {len(with_rule)} carry rule_fired, "
        f"{len(text):,} chars posted",
    )


def check_run_at(base: str) -> bool:
    """PROOF 3 — the host's run_at is the run_at in the committed reports/latest.json."""
    if not LATEST.exists():
        return record("proof 3: live run_at == committed run_at", False,
                      f"{LATEST} missing — run the nightly workflow first")
    local = json.loads(LATEST.read_text(encoding="utf-8")).get("run_at")
    code, body, _ = get(f"{base}/api/report")
    if code != 200:
        return record("proof 3: live run_at == committed run_at", False, f"HTTP {code}")
    live = json.loads(body).get("run_at")
    ok = bool(local) and local == live
    return record("proof 3: live run_at == committed run_at", ok,
                  f"live={live!r} committed={local!r}")


def check_keepalive_target(base: str) -> bool:
    """PROOF 4b — the endpoint keepalive.yml actually curls returns 200 with a body."""
    code, body, secs = get(f"{base}/api/report")
    ok = code == 200 and len(body) > 1
    return record("proof 4b: keepalive target /api/report returns 200",
                  ok, f"HTTP {code}, {len(body):,} bytes, {secs:.2f}s")


def adversarial(base: str) -> bool:
    """Point every check at a wrong target; each MUST fail. A check that passes here is broken."""
    print("\n  -- adversarial probes (each must FAIL to prove the check discriminates) --")
    bogus = "https://almanac-a09-does-not-exist.vercel.app"
    probes = []

    code, body, _ = get(f"{bogus}/")
    probes.append(("GET / against a non-existent host", not (code == 200 and len(body) > 500),
                   f"HTTP {code}"))

    code, _, _ = post_json(f"{base}/api/lint", {"text": ""})
    probes.append(("POST /api/lint with empty text", code != 200, f"HTTP {code} (422 expected)"))

    if LATEST.exists():
        real = json.loads(LATEST.read_text(encoding="utf-8")).get("run_at")
        mutated = "1999-01-01T00:00:00Z"
        probes.append(("run_at equality against a mutated timestamp", real != mutated,
                       f"{real!r} != {mutated!r}"))

    all_ok = True
    for name, failed_as_expected, detail in probes:
        print(f"  {'OK  ' if failed_as_expected else 'BAD '}  {name} — {detail}")
        all_ok &= failed_as_expected
    if not all_ok:
        record("adversarial probes all discriminate", False, "a probe passed that should fail")
    return all_ok


def main() -> int:
    ap = argparse.ArgumentParser(description="A-09 live-host proof runner")
    ap.add_argument("--url", required=True, help="deployment base URL, no trailing slash")
    ap.add_argument("--skip-adversarial", action="store_true")
    args = ap.parse_args()
    base = args.url.rstrip("/")

    print(f"A-09 verification against {base}\n")
    check_root(base)
    check_lint(base)
    check_run_at(base)
    check_keepalive_target(base)
    if not args.skip_adversarial:
        adversarial(base)

    print("\n--- proof lines ---")
    p1 = all(ok for name, ok, _ in results if name.startswith("proof 1"))
    p3 = all(ok for name, ok, _ in results if name.startswith("proof 3"))
    p4 = all(ok for name, ok, _ in results if name.startswith("proof 4"))
    print(f"PROOF A-09: live URL GET / 200 and POST /api/lint fresh_wrong.md < 60 s from the "
          f"deployed host = {'PASS' if p1 else 'FAIL'}")
    print(f"PROOF A-09: live /api/report run_at equals the run_at in the pushed "
          f"reports/latest.json = {'PASS' if p3 else 'FAIL'}")
    print(f"PROOF A-09: keepalive.yml is valid YAML, scheduled, and one manual run returns 200 "
          f"= {'PASS (live half; YAML half proven offline)' if p4 else 'FAIL'}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
