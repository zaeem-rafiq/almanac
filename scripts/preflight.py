#!/usr/bin/env python3
"""A-00 pre-flight gate.

Proves every external surface Almanac depends on is reachable and correctly configured, then
prints and appends the two PROOF lines. Exits 0 only when every check passes.

Design: each check returns (name, ok, detail) and NEVER aborts the run. One pass reports every
problem at once, so a broken environment is fixed in one round-trip instead of six.

RULE: no secret is ever printed — `almanac.cli.mask` emits a short prefix and a length only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from almanac.cli import load_env, mask  # noqa: E402

PROOF_PATH = ROOT / "docs" / "proofs" / "A-00.md"
RULES_PATH = ROOT / ".agents" / "rules" / "almanac.md"
TOKEN_PATH = ROOT / "token.json"
CLIENT_SECRET_PATH = ROOT / "client_secret.json"
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# ADR-000 gap G-1: the base URL for this account's plan is discovered by observation, not
# assumed. The first base that returns a non-empty list wins, and preflight reports which.
FMP_BASES = (
    "https://financialmodelingprep.com/stable",
    "https://financialmodelingprep.com/api/v4",
    "https://financialmodelingprep.com/api/v3",
)
# label -> FMP indicator name, in the order the PROOF line lists them.
FMP_SERIES = {
    "federalFunds": "federalFunds",
    "mortgage30": "30YearFixedRateMortgageAverage",
    "cpi": "CPI",
}


# --------------------------------------------------------------------------- credential-free

def check_imports() -> tuple[str, bool, str]:
    try:
        import fastapi  # noqa: F401
        import srt  # noqa: F401
        import yaml  # noqa: F401
    except Exception as exc:  # pragma: no cover - environment failure
        return ("imports", False, f"{type(exc).__name__}: {exc}")
    return ("imports", True, "srt, fastapi, yaml")


def check_rules() -> tuple[str, bool, str]:
    if not RULES_PATH.is_file():
        return ("rules", False, f"missing {RULES_PATH.relative_to(ROOT)}")
    text = RULES_PATH.read_text(encoding="utf-8")
    has_trigger = any(line.strip() == "trigger: always_on" for line in text.splitlines()[:5])
    has_phrase = "The LLM never decides" in text
    ok = has_trigger and has_phrase
    return ("rules", ok, f"trigger:always_on={has_trigger} phrase={has_phrase}")


def check_remote() -> tuple[str, bool, str]:
    try:
        out = subprocess.run(
            ["git", "remote", "-v"], cwd=ROOT, capture_output=True, text=True, timeout=20
        ).stdout.strip()
    except Exception as exc:  # pragma: no cover
        return ("remote", False, f"{type(exc).__name__}: {exc}")
    if not out:
        return ("remote", False, "no git remote configured")
    if "github.com" not in out:
        return ("remote", False, f"remote is not github.com: {out.splitlines()[0]}")
    origin = next((l for l in out.splitlines() if l.startswith("origin")), out.splitlines()[0])
    return ("remote", True, origin.split("\t")[-1])


# ------------------------------------------------------------------------------- credentialed

def check_llm() -> tuple[str, bool, str]:
    """Prove the model answers AND that Pydantic's $defs/$ref schema is accepted (gap G-3)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("LLM_MODEL")
    if not api_key:
        return ("llm", False, "ANTHROPIC_API_KEY ABSENT")
    if not model:
        return ("llm", False, "LLM_MODEL ABSENT")

    from typing import Literal

    import anthropic
    from pydantic import BaseModel, Field, ValidationError

    class Claim(BaseModel):
        text: str = Field(description="the verbatim sentence containing the number")
        value: float
        unit: Literal["percent", "usd"]

    class Claims(BaseModel):
        claims: list[Claim]

    schema = Claims.model_json_schema()
    if "$defs" not in json.dumps(schema):
        return ("llm", False, "schema lacks $defs — the G-3 question would go untested")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=512,
            tools=[{
                "name": "record_claims",
                "description": "Record every numeric claim found in the text.",
                "input_schema": schema,
            }],
            tool_choice={"type": "tool", "name": "record_claims"},
            messages=[{
                "role": "user",
                "content": "Text: 'The federal funds rate is 4.5% and the limit is 23000 dollars.'",
            }],
        )
    except Exception as exc:
        return ("llm", False, f"{type(exc).__name__}: {str(exc)[:200]}")

    blocks = [b for b in message.content if getattr(b, "type", None) == "tool_use"]
    if not blocks:
        return ("llm", False, f"no tool_use block; stop_reason={message.stop_reason}")
    try:
        parsed = Claims.model_validate(blocks[0].input)
    except ValidationError as exc:
        return ("llm", False, f"tool_use input failed validation: {str(exc)[:200]}")
    return ("llm", True, f"model={model} $defs accepted, {len(parsed.claims)} claim(s) returned")


def _fmp_get(base: str, path: str, params: dict[str, str], api_key: str):
    import requests

    response = requests.get(
        f"{base}/{path}", params={**params, "apikey": api_key}, timeout=30
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    return response.json()


def check_fmp() -> tuple[str, bool, str]:
    """Prove all four series answer, and report how fresh each one is (gap G-1, KTD3)."""
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        return ("fmp", False, "FMP_API_KEY ABSENT")

    base_used, last_error = None, "no base tried"
    for base in FMP_BASES:
        try:
            rows = _fmp_get(base, "economics-indicators", {"name": "federalFunds"}, api_key)
            if isinstance(rows, list) and rows:
                base_used = base
                break
            last_error = f"{base} -> 200 but empty list"
        except Exception as exc:
            last_error = f"{base} -> {type(exc).__name__}: {str(exc)[:80]}"
    if base_used is None:
        return ("fmp", False, f"no working base URL ({last_error})")

    freshness, failures = [], []
    for label, name in FMP_SERIES.items():
        try:
            rows = _fmp_get(base_used, "economics-indicators", {"name": name}, api_key)
        except Exception as exc:
            failures.append(f"{label}: {type(exc).__name__}")
            continue
        if not isinstance(rows, list) or not rows:
            failures.append(f"{label}: 0 rows")
            continue
        newest = max(r.get("date", "") for r in rows)
        freshness.append(f"{label}@{newest}({len(rows)}r)")

    try:
        rows = _fmp_get(base_used, "treasury-rates", {}, api_key)
        if not isinstance(rows, list) or not rows:
            failures.append("treasury10: 0 rows")
        else:
            newest_row = max(rows, key=lambda r: r.get("date", ""))
            if newest_row.get("year10") is None:
                failures.append("treasury10: newest row has no year10")
            else:
                freshness.append(f"treasury10@{newest_row['date']}({len(rows)}r)")
    except Exception as exc:
        failures.append(f"treasury10: {type(exc).__name__}")

    base_label = base_used.rsplit("/", 1)[-1]
    detail = f"base={base_label} " + " ".join(freshness)
    if failures:
        return ("fmp", False, f"{detail} FAILED[{'; '.join(failures)}]")
    return ("fmp", True, detail)


def check_youtube() -> tuple[str, bool, str]:
    """Prove owner OAuth completes AND that the authorised channel is the TEST channel."""
    expected = os.environ.get("ALMANAC_TEST_CHANNEL_ID")
    if not expected:
        return ("youtube_oauth", False, "ALMANAC_TEST_CHANNEL_ID ABSENT")

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_PATH.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), YOUTUBE_SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            return ("youtube_oauth", False, f"token refresh failed: {type(exc).__name__}")
    if not creds or not creds.valid:
        if not CLIENT_SECRET_PATH.is_file():
            return ("youtube_oauth", False, f"missing {CLIENT_SECRET_PATH.name} in repo root")
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRET_PATH), YOUTUBE_SCOPES
            )
            creds = flow.run_local_server(port=0)
        except Exception as exc:
            return ("youtube_oauth", False, f"consent failed: {type(exc).__name__}: {str(exc)[:120]}")
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    try:
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        items = youtube.channels().list(part="id", mine=True).execute().get("items", [])
    except Exception as exc:
        return ("youtube_oauth", False, f"channels.list failed: {type(exc).__name__}: {str(exc)[:120]}")

    if not items:
        return ("youtube_oauth", False, "channels.list(mine=True) returned no items")
    actual = items[0]["id"]
    if actual != expected:
        return (
            "youtube_oauth", False,
            f"authorised channel {actual[:8]}… != ALMANAC_TEST_CHANNEL_ID {expected[:8]}…",
        )
    return ("youtube_oauth", True, f"channel={actual[:8]}… authorised {datetime.now(timezone.utc):%Y-%m-%d}")


# ------------------------------------------------------------------------------------ runner

CHECKS = (check_imports, check_rules, check_remote, check_llm, check_fmp, check_youtube)


def main(argv: list[str]) -> int:
    load_env(os.environ.get("ALMANAC_ENV_FILE"))
    write_proof = "--no-proof" not in argv

    print("Almanac pre-flight (A-00)\n" + "-" * 60)
    for name in ("ANTHROPIC_API_KEY", "FMP_API_KEY", "LLM_MODEL", "ALMANAC_TEST_CHANNEL_ID"):
        print(f"  {name:<24} {mask(os.environ.get(name))}")
    print("-" * 60)

    results = {}
    for check in CHECKS:
        name, ok, detail = check()
        results[name] = (ok, detail)
        print(f"  {name:<14} = {'PASS' if ok else 'FAIL'}  {detail}")

    def verdict(name: str) -> str:
        return "PASS" if results[name][0] else "FAIL"

    fmp_series = "federalFunds,mortgage30,cpi,treasury10"
    channel = "UC…"
    if results["youtube_oauth"][0]:
        channel = results["youtube_oauth"][1].split("channel=")[-1].split()[0]

    proof_lines = [
        f"PROOF A-00: llm={verdict('llm')} fmp={verdict('fmp')}({fmp_series}) "
        f"youtube_oauth={verdict('youtube_oauth')}(channel={channel}) "
        f"imports={verdict('imports')} remote={verdict('remote')}",
        'PROOF A-00: rules file has trigger: always_on and contains '
        f'"The LLM never decides" = {verdict("rules")}',
    ]

    print("-" * 60)
    for line in proof_lines:
        print(line)

    if write_proof:
        PROOF_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        with PROOF_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"\n## Run {stamp}\n\n")
            for name, (ok, detail) in results.items():
                handle.write(f"- `{name}` = {'PASS' if ok else 'FAIL'} — {detail}\n")
            handle.write("\n```\n" + "\n".join(proof_lines) + "\n```\n")

    return 0 if all(ok for ok, _ in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
