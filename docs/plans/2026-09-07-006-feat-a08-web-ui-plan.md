# A-08 / HAC-44 — the two-pane web page

**Milestone** M4 · **Budget** 2.5h / 50 turns · 3 points · URGENT
**Branch** `claude/stoic-babbage-5e3238` · **Base** `main` @ `d2eb390`

## What this issue is

`web/app.py` (FastAPI) + `web/static/index.html` (one file, inline CSS/JS, no framework, no build).
Two panes: **Catalog watch** reads a scan that already happened; **Lint a script** runs the
pipeline live on pasted text. No auth, no visitor keys.

Both existing files are 1-line placeholders from A-00 (`0f21782`). This issue writes them.

## Baseline, measured before any code

* `venv/bin/pytest tests/ -q` -> **201 passed** (21.99s).
* `venv/bin/python -m almanac scan --source corpus --out reports/` -> 29.9s,
  `stale_material=6 stale_immaterial=1 correct=1 unresolved=3 skip=55` = 66 verdicts / 5 videos.
  Inside the issue's stated 64-77 band.
* `venv` lives in the main checkout, not the worktree; symlinked in (gitignored). `pip` is absent
  from it, so `uv pip install --python venv/bin/python` is the install path.

## Architecture — the page reads, it does not re-derive

`reports/latest.json` is gitignored and is a build artifact. `GET /api/report` **serves that file**;
it never triggers a scan. A scan costs ~30s and live model calls, so a page that scanned on load
would bill the visitor's page-view to Zaeem's API key.

The endpoints reuse the CLI's own pipeline functions rather than re-implementing them, so a
verdict on the page is the same object `almanac lint` prints:

| endpoint | reuses |
|---|---|
| `POST /api/lint` | `extract.read_source` -> `cli._judged` -> `report.to_row` |
| `GET /api/report` | `reports/latest.json` verbatim, plus a `video_id` join (below) |
| `POST /api/apply` | `report` rows + `corpus/**/meta.json`, `difflib.unified_diff` |

**The `video_id` join.** `report.VideoReport` carries `source_id / title / published_at / verdicts /
counts` — no `video_id`, so nothing in the report can build a YouTube link. `report.py` is not
rewritten for this; `web/app.py` joins `corpus/channel/*/meta.json` onto each video at serve time.
A link target is presentation, not a decision, and `report.py` is the module that owns the decision
shape.

**`POST /api/lint` builds its `Source` through `extract.read_source`**, by writing the pasted text
to a temp `.md` and `dataclasses.replace`-ing the `source_id`. Re-implementing `read_script`'s line
flattening in the web layer would let the page's locators drift from the CLI's; using the real
reader means they cannot.

## Protected paths — respected

`almanac/judge.py` and `almanac/extract.py` are **not touched**. `facts/**`, `evals/**`,
`corpus/**` are read-only here. If a verdict looks wrong on the page that is a finding for the
walkthrough, never an edit to the engine.

## Design — "almanac page"

Direction fixed by the issue and run through the `kole-jain-ui-design` gate: paper-white ground,
one serif display face (system serif stack — no webfont, because a CDN font is a new dependency
and a new failure mode in a single-file page), tabular monospace figures, thin rules, status chips
as ink stamps, no gradients, no cards-in-cards.

**One saturated colour only: oxblood, reserved for `STALE`.** Five statuses have to be
distinguishable, and the gate's "two colours beyond neutrals" rule plus "one clear focal point"
both point the same way — the thing you are hunting for gets the ink, the other four are separated
by weight and border treatment in neutrals. `stale_material` is filled, `stale_immaterial` is the
same hue outlined, `correct`/`unresolved`/`skip` are neutral (unresolved dashed: not determined).

**The verdict row is the hero.** Quote @ locator -> claim -> current value + effective date +
source link -> chip -> `rule_fired` -> drafted note. Current value and its source link readable in
one glance; on a 390px viewport the row reflows to stacked labelled lines rather than scrolling
sideways.

## The honesty constraint — what the page may and may not say

Read `docs/decisions/ADR-002-eval-loop.md` (on `claude/angry-euler-66e47d`, the A-05 branch; not
merged). Copy on this page is bounded by what A-05 actually measured:

**May say** — attributed to A-05, with the run count:
extraction recall 89% (48/54) · `claim_type` accuracy 98% · `stale_material` precision 1.00 /
recall 1.00 · hard-negative false positives 0 (22 matched rows) · `skip` recall 0.81 ·
the judge is deterministic and rule-driven: **0 status disagreements across five live runs**.

**May not say** — every number is always caught; results are reproducible run-to-run; any accuracy
figure A-05 did not measure. The page must state, in its own voice, that **extraction is not
reproducible** (65-77 verdicts on identical input; the eval gate fails 2 runs in 5) and that the
`temperature=0.0` fix is **proposed in ADR-002 D-5 and not applied**, pending Zaeem.

Understating is fine. Overstating is the one unrecoverable mistake on a page built to demonstrate
honesty.

### Finding, carried into the build rather than papered over

The issue specifies the V4 row must show **"5 numbers read, 0 flagged (illustrative)"**. This run
produces **19** verdicts for V4 (14 `illustrative`, 4 `other`, 1 `structural`, all
`R2.unjudged_claim_type`). The "5" is from a different run — extraction is not reproducible, which
is the very defect A-05 recorded. Hardcoding `5` would print a fabricated number on the honesty
page. **The row renders the pattern from the data**: `"<N> numbers read, 0 flagged (<dominant
type>)"`, N from the report. Reported as a finding in the walkthrough.

## The write guard

`almanac/youtube.py` has **no `videos.update` code at all** — A-06 shipped read-only (verified:
`grep` finds no update call; the module docstring says so). So `/api/apply` has no write path to
disable; it composes the would-be description (`meta.json` description + the drafted `📌 Update`
notes for that video's `stale_material` rows) and returns a unified diff.

A web endpoint has no `--apply`, so **`/api/apply` is dry-run unconditionally** — it does not
consult `ALMANAC_WRITE` at all, and proof 3 is run a second time with `ALMANAC_WRITE=true` to show
the flag cannot change the outcome. That is stronger than the issue's "never writes unless
`ALMANAC_WRITE=true`", and the stronger reading is the one the WRITE GUARD section asks for.

## Proofs — and the adversarial probe each negative needs

A negative assertion proves nothing on its own: a counter wired to the wrong object and a genuinely
clean run look identical. Proofs 3 and 4 are both negatives, so each one **first shows its counter
registering non-zero**, then shows it reading zero on the real target.

| # | proof | how, and the probe |
|---|---|---|
| 1 | `GET /` 200, `GET /api/report` 200 with 5 videos | assert **both sides**: `len(report["videos"]) == 5` **and** 5 dirs under `corpus/channel/`, and each has a `video_id` + non-empty `verdicts`. "0 videos, 0 errors" must not pass. |
| 2 | `POST /api/lint` on `fresh_wrong.md` -> >=1 `stale_material`, >=2 `skip`, < 45s | live model call. Grounded: catalog `ira_contribution` = **7,500** vs the script's **$7,000** -> exact-match domain -> `R5.stale_material`; the illustrative numbers supply the skips. |
| 3 | `POST /api/apply`, `ALMANAC_WRITE` unset -> diff returned, `videos.update` count **0** | instrument, do not trust the guard. Patch `googleapiclient.discovery.build` — the chokepoint **any** write must pass through, including a path I did not write — to return a counting stub. **Probe: drive the stub by hand, assert count == 1.** Reset, POST, assert count == 0 **and** the diff is non-empty. Repeat with `ALMANAC_WRITE=true`. |
| 4 | page renders, JS console errors **0** at 1280px and 390px | playwright headless (installed via `uv`, driving system Chrome with `channel="chrome"` — no 150MB browser download). **Probe: point the same counter at a deliberately broken page, assert count >= 1.** Then assert 0 on the real page at both widths **and** that the page actually rendered — 5 video rows, both panes present. |

`monkeypatch.setitem(sys.modules, ...)` is deliberately **not** used: it does not intercept
`from pkg.mod import x` once the real module is imported, because the package attribute wins.
Patching is done on the already-imported module object.

## Steps

1. `web/app.py` — 4 endpoints, `MAX_LINT_CHARS = 8000`, `video_id` join, diff composer.
2. `web/static/index.html` — one file, two panes, the design above.
3. `tests/test_web.py` — endpoint contracts + the write-guard proof with its probe.
4. `tests/test_web_browser.py` — playwright console-error proof with its probe.
5. Run all four proofs, print them, append to `docs/proofs/A-08.md`.
6. `docs/walkthroughs/A-08.md`, commit in logical units, rebase onto `origin/main`, push, PR.

## Stop condition

Done only when all four PROOF lines read PASS and `pytest tests/ -q` is still green at >= 201.
On the bound: `docs/blockers/A-08.md`, print `BLOCKED A-08`, stop.
