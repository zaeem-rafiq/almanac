# Almanac

**Every gate in this gallery checks the video you're about to publish. Almanac checks the ones you already did.**

Almanac reads every number in a finance creator's scripts and back catalog, checks each one
against its own rates table (written by `rates --refresh`, straight from Treasury, BLS,
Freddie Mac and the New York Fed) and a sourced IRS facts table, and drafts the
"📌 Update" note for the videos that went stale.

## The principle

**The LLM reads, the code decides.**

`extract.py` and `notes.py` are the only modules that call a model. Every verdict comes from
`judge.py` rules R1–R5, and every verdict carries `rule_fired`. A change that puts a verdict in
a prompt is wrong.

## Status

Milestone M5 — submission prep. Live at <https://almanac-gamma.vercel.app>. See `docs/plans/`
for the current plan and `docs/proofs/` for per-issue evidence.

## Layout

| Path | Role |
|---|---|
| `almanac/` | package: `cli` `catalog` `extract` `judge` `notes` `report` `youtube` `models` |
| `facts/catalog.yaml` | sourced facts; `manual` entries are supplied by a human with a primary-source URL |
| `facts/rates.json` | Almanac's own rates table, written by `rates --refresh` |
| `corpus/` | synthetic channel and scripts used for the demo |
| `evals/claims.jsonl` | human-labeled claims |
| `docs/{plans,proofs,blockers,decisions}/` | plans, per-issue proof, blockers, ADRs |

## Licence

MIT — see `LICENSE`.
