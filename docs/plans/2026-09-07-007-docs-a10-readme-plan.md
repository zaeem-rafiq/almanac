# A-10 (HAC-46) — the README a judge reads in three minutes

**Milestone:** M5 · **Budget:** 1.5h / 30 turns · **Kind:** documentation only.

## Corrections to the issue text, verified before starting

| issue says | repo says | evidence |
|---|---|---|
| "catalog (FRED + sourced IRS table)" | no FRED anywhere; rates fetched via **FMP**, each row linked to its **primary source** | `grep -ohE "https://[a-z0-9./-]+" facts/rates.json` → data.bls.gov, home.treasury.gov, www.freddiemac.com, www.newyorkfed.org |
| extractor is Anthropic | **gemini-3.8-flash on Google Vertex AI** | `grep -rn anthropic --include="*.py" almanac/ web/` → no hits; `almanac/extract.py:435` `DEFAULT_MODEL` |
| "R1–R5" as named in the spec | rules were **renumbered** at `609af65` | `docs/walkthroughs/A-04.md:68`; real `rule_fired` values read from `almanac/judge.py` |

## Things the issue assumes exist but do not — I create them

* `.env.example` — absent. Five real variables + one optional. **No `ANTHROPIC_API_KEY`, no values.**
* `requirements.txt` — absent, and the issue's own "Try it" block installs from it. Without this
  file the README's second instruction fails on a fresh clone, so the README would ship a lie.
* `docs/architecture.mmd` + `docs/architecture.svg` — absent.
* `.gitignore` — `.env*` currently swallows `.env.example`; needs a `!.env.example` negation.

## Pre-condition shortfall, carried not blocked

A-09 (live URL) is **not** merged — PR #6 is open pending `vercel login` + three repo secrets.
The "Try it in 60 seconds" block is therefore written so the **local path is standalone and
complete**, with the hosted URL as an explicitly marked placeholder. No URL is invented.

## Steps, each with its check

1. Regenerate `reports/latest.json` from a real corpus scan (it currently holds an empty YouTube
   run, 0 videos). — check: `counts.stale_material >= 1`.
2. Serve `web/app.py`, screenshot a real `stale_material` verdict row. — check: the PNG exists and
   the row's rendered numbers match `reports/latest.json`.
3. `docs/architecture.mmd` → `docs/architecture.svg`. — check: proof 2, every module name in the
   SVG resolves to a real file in `almanac/`.
4. Write `README.md` in the issue's six-section order. — check: proofs 1, 3, 4.
5. `scripts/check_readme_numbers.py` — every number in the README traces to `evals/results.json`,
   `judge.py` constants, or `facts/catalog.yaml`. — check: it must **FAIL on a deliberately wrong
   number** before its PASS counts (Project Rules: a negative assertion proves nothing until it is
   shown able to register a presence).
6. `pytest tests/ -q` unchanged. — check: same pass count as before the branch.

## Protected paths honoured

`almanac/**`, `facts/**`, `evals/**`, `corpus/**` are read-only here. If a README claim needs a
code change, it is reported, not made.
