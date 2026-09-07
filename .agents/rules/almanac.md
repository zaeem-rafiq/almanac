---
trigger: always_on
---

# Almanac — Project Rules

* Repo `almanac/` (MIT). Python 3.12 in a venv. Packages: `anthropic`, `google-api-python-client`, `google-auth-oauthlib`, `requests`, `pydantic`, `pyyaml`, `srt`, `fastapi`, `uvicorn`, `pytest`. Verify every library call against the installed package docs before using it.
* The LLM never decides. `extract.py` and `notes.py` are the only modules that call the model. Verdicts come from `judge.py` rules only; every verdict carries `rule_fired`. A change that puts a verdict in a prompt is wrong.
* No invented facts. Every `source: manual` entry in `facts/catalog.yaml` is supplied by Zaeem with an irs.gov / ssa.gov / treasurydirect.gov URL. The agent may scaffold entries with `value: null`; it may not fill values. Tests fail on null values and on URLs that don't return 200.
* Official APIs only: YouTube Data API v3 (owner OAuth); FMP for the four market rates, written to `facts/rates.json` by `rates --refresh` and read from there at runtime — no module other than `catalog.refresh_rates()` calls FMP. Never `youtube-transcript-api`, `yt-dlp`, or HTML scraping.
* Write guard: `videos.update` runs only when `ALMANAC_WRITE=true` AND the target channel id equals `ALMANAC_TEST_CHANNEL_ID` AND the CLI was given `--apply`. Default is dry-run (prints the diff). Nothing else mutates YouTube.
* Secrets only from `.env` (gitignored) locally and GitHub Actions secrets / host env in the cloud. Never print a secret; print a prefix check only.
* Proof lines: print `PROOF A-xx: <check> = PASS|FAIL` to the transcript AND append the same lines to `docs/proofs/A-xx.md`. An issue is done only when every listed proof is PASS.
* Failure handoff: after 2 failed attempts at the same step, or 30 minutes without a passing proof, write `docs/blockers/A-xx.md` (what was tried, exact error, hypothesis, smallest next step), print `BLOCKED A-xx`, stop.
* Protected paths (all issues): `LICENSE`, `.env*`, `token.json`, `docs/proofs/**`, `docs/blockers/**`, `docs/decisions/**` (append-only), plus the per-issue list. `facts/catalog.yaml` values and `evals/claims.jsonl` are protected after A-01/A-02 — only Zaeem edits them. `almanac/models.py` is protected after A-03.
* Turn bound = hard stop. When the bound is hit, write the blocker doc even if close to done.
* Produce a plan artifact before code and a walkthrough artifact after each issue.
