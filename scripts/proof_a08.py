"""A-08 proof harness — the four PROOF lines for the web page.

Prints each line to the transcript and appends the same lines to docs/proofs/A-08.md.

Two of the four proofs are pure negatives, and a negative assertion proves nothing on its own:
a counter wired to the wrong object reads zero exactly like a clean run. So each one runs its
probe FIRST and fails if the probe does not go red.

  * Proof 3 counts `videos.update` calls. The probe drives the counting stub by hand and asserts
    the count reaches 1 before the endpoint is allowed to assert 0. The stub is installed at
    `googleapiclient.discovery.build` — the chokepoint every YouTube call passes through,
    including one a future edit might write without touching `almanac.youtube`.

  * Proof 4 counts JS console errors. The probe loads a deliberately broken page with the same
    listener and asserts it registers, before the real page is allowed to report zero.

Proof 1 asserts the SIZE of what it checks on BOTH sides — 5 videos in the report AND 5 video
directories in the corpus — because "0 videos, 0 errors" passes a sloppy check.

Proof 2 is the only one that spends money: a live extraction + judge + notes pass over
corpus/scripts/fresh_wrong.md.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from almanac.cli import load_env  # noqa: E402

PROOF_DOC = REPO_ROOT / "docs" / "proofs" / "A-08.md"
CORPUS_CHANNEL = REPO_ROOT / "corpus" / "channel"
FRESH_WRONG = REPO_ROOT / "corpus" / "scripts" / "fresh_wrong.md"

LINT_BUDGET_S = 45.0
VIEWPORTS = {"1280 px": (1280, 800), "390 px": (390, 844)}

results: list[str] = []
notes: list[str] = []


def record(line: str) -> None:
    print(line, flush=True)
    results.append(line)


def note(line: str) -> None:
    print(f"    {line}", flush=True)
    notes.append(line)


# --------------------------------------------------------------------------------- the server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve():
    """Run the real app over HTTP so the browser proof drives the same code the tests do."""
    import uvicorn

    from web.app import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start within 20s")
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server, thread


# ------------------------------------------------------------------------------------ proof 1


def proof_one(client) -> bool:
    index = client.get("/")
    report_response = client.get("/api/report")

    ok_index = index.status_code == 200 and len(index.text) > 5_000
    note(f"GET /            -> {index.status_code}, {len(index.text):,} bytes")

    if report_response.status_code != 200:
        note(f"GET /api/report  -> {report_response.status_code} {report_response.text[:120]}")
        return False

    report = report_response.json()
    videos = report["videos"]
    corpus_dirs = sorted(p for p in CORPUS_CHANNEL.iterdir() if p.is_dir())

    # BOTH sides. A report of 0 videos against a corpus of 0 videos must not pass.
    ok_count = len(videos) == 5 and len(corpus_dirs) == 5
    ok_rows = all(v["verdicts"] and v["video_id"] for v in videos)
    ok_rules = all(row["rule_fired"] for v in videos for row in v["verdicts"])

    note(f"GET /api/report  -> 200, {len(videos)} videos, {len(corpus_dirs)} corpus dirs")
    for v in videos:
        note(f"  {v['video_id']}  {v['read_count']:>3} read  {v['flagged_count']:>2} flagged  "
             f"{(v['title'] or '')[:44]}")
    note(f"every verdict carries rule_fired: {ok_rules}")

    return bool(ok_index and ok_count and ok_rows and ok_rules)


# ------------------------------------------------------------------------------------ proof 2


def proof_two(client) -> bool:
    text = FRESH_WRONG.read_text(encoding="utf-8")
    note(f"POST /api/lint <- corpus/scripts/fresh_wrong.md ({len(text):,} chars), live model call")

    started = time.monotonic()
    response = client.post("/api/lint", json={"text": text})
    elapsed = time.monotonic() - started

    if response.status_code != 200:
        note(f"-> {response.status_code} {response.text[:200]}")
        return False

    body = response.json()
    counts = body["counts"]
    stale_material = counts.get("stale_material", 0)
    skips = counts.get("skip", 0)

    note(f"-> 200 in {elapsed:.1f}s (server-measured {body['elapsed_ms'] / 1000:.1f}s), "
         f"{len(body['verdicts'])} verdicts")
    note("   " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for row in body["verdicts"]:
        if row["status"] != "skip":
            note(f"   {row['status']:<18} {row['rule_fired']:<26} "
                 f"said={row['claimed_value']} now={row['current_value']}")

    return bool(stale_material >= 1 and skips >= 2 and elapsed < LINT_BUDGET_S)


# ------------------------------------------------------------------------------------ proof 3


class UpdateCounter:
    """Stands in for the YouTube service and counts `videos().update()`."""

    def __init__(self) -> None:
        self.update_calls = 0

    def videos(self):
        return self

    def update(self, *args, **kwargs):
        self.update_calls += 1
        return self

    def list(self, *args, **kwargs):
        return self

    def execute(self, *args, **kwargs):
        return {"items": []}


def proof_three(client) -> bool:
    import googleapiclient.discovery

    import almanac.youtube as youtube_module

    stub = UpdateCounter()
    real_build = googleapiclient.discovery.build
    real_client = youtube_module.build_client

    # setattr on the imported module object — NOT monkeypatch.setitem(sys.modules, ...), which
    # does not intercept `from pkg.mod import x` once the real module has been imported.
    googleapiclient.discovery.build = lambda *a, **k: stub
    youtube_module.build_client = lambda *a, **k: stub

    try:
        # THE PROBE: prove the counter can register non-zero before any zero is believed.
        probe_service = googleapiclient.discovery.build("youtube", "v3")
        probe_service.videos().update(part="snippet", body={}).execute()
        probe_ok = stub.update_calls == 1
        note(f"probe: drove the stub by hand -> videos.update count = {stub.update_calls} "
             f"({'counter registers' if probe_ok else 'COUNTER IS DEAD'})")
        if not probe_ok:
            return False

        stub.update_calls = 0

        import os

        had_flag = "ALMANAC_WRITE" in os.environ
        previous = os.environ.pop("ALMANAC_WRITE", None)
        try:
            response = client.post("/api/apply", json={})
        finally:
            if had_flag and previous is not None:
                os.environ["ALMANAC_WRITE"] = previous

        if response.status_code != 200:
            note(f"POST /api/apply -> {response.status_code} {response.text[:200]}")
            return False

        body = response.json()
        changed = [c for c in body["changes"] if c["changed"]]
        diffs_ok = bool(changed) and all(c["diff"].strip() for c in changed)

        note(f"POST /api/apply (ALMANAC_WRITE unset) -> 200, dry_run={body['dry_run']}, "
             f"wrote={body['wrote']}, {len(changed)} description(s) changed")
        for c in changed:
            first = next((ln for ln in c["diff"].splitlines()
                          if ln.startswith("+") and not ln.startswith("+++")
                          and ln[1:].strip()), "")
            note(f"  {c['video_id']}  +{c['note_count']} note(s)  {first[:78]}")
        note(f"videos.update call count after the request = {stub.update_calls}")

        # The endpoint is dry-run UNCONDITIONALLY: the flag must not be able to change this.
        os.environ["ALMANAC_WRITE"] = "true"
        try:
            forced = client.post("/api/apply", json={}).json()
        finally:
            os.environ.pop("ALMANAC_WRITE", None)
            if had_flag and previous is not None:
                os.environ["ALMANAC_WRITE"] = previous
        note(f"re-run with ALMANAC_WRITE=true -> dry_run={forced['dry_run']}, "
             f"wrote={forced['wrote']}, videos.update count = {stub.update_calls}")

        return bool(
            body["dry_run"] is True and body["wrote"] is False and diffs_ok
            and forced["dry_run"] is True and forced["wrote"] is False
            and stub.update_calls == 0
        )
    finally:
        googleapiclient.discovery.build = real_build
        youtube_module.build_client = real_client


# ------------------------------------------------------------------------------------ proof 4


def proof_four(base_url: str) -> bool:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            # THE PROBE: the same listener, pointed at a page that genuinely fails.
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            probe: list[str] = []
            page.on("console", lambda m: probe.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: probe.append(str(e)))
            page.goto(base_url + "/api/scripts", wait_until="networkidle")
            page.evaluate("() => { console.error('probe: deliberate console error'); }")
            page.evaluate(
                "() => { window.setTimeout(function () "
                "{ throw new Error('probe: deliberate uncaught error'); }, 0); }"
            )
            page.wait_for_timeout(300)
            context.close()

            note(f"probe: broken page -> console error count = {len(probe)} "
                 f"({'counter registers' if len(probe) >= 2 else 'COUNTER IS DEAD'})")
            if len(probe) < 2:
                return False

            every_ok = True
            for label, (width, height) in VIEWPORTS.items():
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                errors: list[str] = []
                page.on("console",
                        lambda m: errors.append(f"console.{m.type}: {m.text}")
                        if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

                page.goto(base_url, wait_until="networkidle")
                page.wait_for_selector("details.video", timeout=15_000)
                page.wait_for_timeout(400)

                videos = len(page.query_selector_all("details.video"))
                rows = len(page.query_selector_all("article.v"))
                overflow = page.evaluate(
                    "() => document.documentElement.scrollWidth "
                    "- document.documentElement.clientWidth"
                )
                preloaded = "The limit is $7,000 this year." in page.input_value("#lint-text")
                context.close()

                # Size, not just silence: a page that rendered nothing also logs nothing.
                rendered = videos == 5 and rows >= 1 and preloaded and overflow <= 1
                note(f"{label:<8} -> {videos} video rows, {rows} verdict rows, "
                     f"sideways overflow {overflow}px, script preloaded={preloaded}, "
                     f"console error count = {len(errors)}")
                for err in errors:
                    note(f"    {err}")
                every_ok = every_ok and rendered and not errors

            return every_ok
        finally:
            browser.close()


# ---------------------------------------------------------------------------------------- run


def main() -> int:
    load_env()
    from fastapi.testclient import TestClient

    from web.app import app

    client = TestClient(app)
    base_url, server, thread = serve()

    checks = [
        ("GET / 200, GET /api/report 200 with 5 videos", lambda: proof_one(client)),
        ("POST /api/lint on fresh_wrong.md returns >=1 stale_material and >=2 skip in < 45 s",
         lambda: proof_two(client)),
        ("POST /api/apply with ALMANAC_WRITE unset → diff returned, videos.update call count = 0",
         lambda: proof_three(client)),
        ("page renders with JS console error count = 0 (playwright headless) at 1280 px and 390 px",
         lambda: proof_four(base_url)),
    ]

    passed = 0
    try:
        for title, check in checks:
            print(f"\n--- {title}", flush=True)
            try:
                ok = check()
            except Exception as exc:  # a proof that errored is a proof that failed
                note(f"raised {type(exc).__name__}: {exc}")
                ok = False
            record(f"PROOF A-08: {title} = {'PASS' if ok else 'FAIL'}")
            passed += int(ok)
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    PROOF_DOC.parent.mkdir(parents=True, exist_ok=True)
    existing = PROOF_DOC.read_text(encoding="utf-8") if PROOF_DOC.is_file() else (
        "# A-08 (HAC-44) — Proofs: the two-pane web page\n\n"
        "Harness: `scripts/proof_a08.py` (live) and `tests/test_web.py` + "
        "`tests/test_web_browser.py` (regression).\n"
        "Each run appends a dated section below. Proofs 3 and 4 are negative assertions and each "
        "one runs an adversarial probe first — a counter that cannot register non-zero fails the "
        "proof before the zero is believed.\n"
    )
    block = [f"\n## Run {stamp}\n", "```"]
    block += results
    block += ["```", "", "<details><summary>Observed</summary>", "", "```"]
    block += notes
    block += ["```", "", "</details>", ""]
    PROOF_DOC.write_text(existing + "\n".join(block), encoding="utf-8")

    print(f"\n{passed}/{len(checks)} proofs PASS · appended to {PROOF_DOC.relative_to(REPO_ROOT)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
