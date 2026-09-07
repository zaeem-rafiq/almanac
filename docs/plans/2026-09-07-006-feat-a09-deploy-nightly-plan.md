# A-09 (HAC-45) — deploy + nightly + keepalive

Milestone M4 · budget 1.5h / 35 turns · written before any code.

## What A-09 owns

Deploy configuration and CI only. **No engine logic.** `almanac/**`, `facts/**`, `evals/**`,
`corpus/**` are untouched. `web/app.py` and `web/static/index.html` belong to A-08 and are not
edited here.

New files: `api/index.py`, `vercel.json`, `.vercelignore`, `requirements.txt`.
Rewritten: `.github/workflows/nightly.yml`, `.github/workflows/keepalive.yml`.
Edited: `.gitignore` (one hunk, explained below).

## Three findings that shape the plan

**F-1 — A-08's app is on a branch, not on `main`.** `web/app.py` at `origin/main` is a 57-byte
placeholder docstring. The real 15,232-byte app lives at `origin/claude/stoic-babbage-5e3238`
and exposes `/`, `/api/report`, `/api/lint`, `/api/apply`, `/api/scripts`. It has no open PR.
A deploy taken from `main` today serves a module with no `app` object and 500s on every route.
So proofs 1 and 3 are gated on A-08 landing **as well as** on `vercel login`. This is recorded,
not worked around: A-09 does not edit or vendor A-08's file.

**F-2 — the repo has no `requirements.txt` and no `pyproject.toml`.** Both the Vercel build and
the nightly job's `pip install` need one. Writing it is deploy configuration, so it is in scope.
Versions are pinned to exactly what the working venv resolves, read from its `dist-info`
directories (the venv has no `pip` module, so `pip freeze` was not available).

**F-3 — `web/` has no `__init__.py`.** `from web.app import app` therefore relies on PEP 420
namespace packages and on the repo root being on `sys.path`. On Vercel the function's cwd is not
guaranteed to be the repo root, so `api/index.py` inserts the root explicitly rather than
assuming it.

## Steps, each with its check

| # | Step | Check |
|---|---|---|
| 1 | `requirements.txt` pinned from installed dist-info | `pip install -r` resolves in the Actions runner (proof 2) |
| 2 | `api/index.py` re-exports `web.app:app` with an explicit `sys.path` root | import smoke test against A-08's branch content in a scratch tree |
| 3 | `vercel.json`: `maxDuration: 60`, catch-all rewrite, `includeFiles` for the data dirs | JSON parses; keys asserted by a local test |
| 4 | `.gitignore` — allow `reports/latest.json` + `reports/<date>.md`, keep ad-hoc output ignored | `git check-ignore` says allowed for those two, still ignored for a scratch file |
| 5 | `nightly.yml` — cron `0 7 * * *` + dispatch, rates refresh, scan with **corpus fallback**, commit, push | YAML parses; fallback branch exercised locally with the real CLI |
| 6 | `keepalive.yml` — every 10 min, 12–22 UTC, Sep 8 only, curl `/api/report` | YAML parses; schedule fields asserted; adversarial probe must fail |
| 7 | Proofs + walkthrough; PENDING recorded with reasons | `docs/proofs/A-09.md` |

## Proof status decided in advance

| Proof | Runnable now? |
|---|---|
| 1 — live `GET /` 200 and `POST /api/lint` < 60 s | **No.** Needs `vercel login` (Zaeem) **and** A-08 on `main` (F-1). |
| 2 — `workflow_dispatch` nightly green, pushes a commit touching `reports/latest.json` | **No.** Needs `ANTHROPIC_API_KEY` + `FMP_API_KEY` repo secrets (Zaeem). Local dry-run of the identical command sequence stands in as evidence, labelled as such. |
| 3 — live `/api/report` `run_at` equals pushed `reports/latest.json` `run_at` | **No.** Depends on 1 and 2. |
| 4 — keepalive valid YAML, scheduled, one manual run returns 200 | **Split.** YAML + schedule assertions run now with an adversarial probe; the 200 needs the live URL. |

Every PENDING carries its reason in `docs/proofs/A-09.md`, following the A-06 deferred-proof
precedent. Nothing is claimed from config that merely looks right — ADR-000 §10 is the standing
reminder of what that costs.

## Adversarial probes (required, not optional)

A check that only ever passes proves nothing. Each local assertion is run twice: once against the
real artifact, once against a deliberately wrong one that must FAIL.

- keepalive schedule assertion → also run against a copy with `schedule:` deleted; must fail.
- `run_at` equality → also run against a mutated `run_at`; must fail.
- `.gitignore` allow-list → also run against `reports/scratch.json`; must stay ignored.
- `maxDuration` assertion → also run against `maxDuration: 10`; must fail.

## Bound

Hard stop at 35 turns. On stop: `docs/blockers/A-09.md`, print `BLOCKED A-09`.
Fallback host order Vercel → Fly.io → Render. `flyctl` is not installed and `render` is not
installed, so a fallback is itself gated on Zaeem installing a CLI — noted in the checklist.
