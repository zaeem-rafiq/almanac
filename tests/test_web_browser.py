"""A-08 proof 4: the page renders with zero JS console errors, at 1280px and 390px.

**This is a negative assertion, so it does not stand alone.** A console-error counter pointed at
the wrong page and a genuinely clean page produce identical output: zero. So
`test_the_console_counter_can_register_non_zero` drives the *same* counter against a deliberately
broken page first. If that probe ever stops failing, the zeroes below mean nothing and the suite
says so.

The second guard is size: a page that failed to render also logs no errors. Every viewport check
asserts what actually appeared — both panes, five video rows, the video ids — not merely the
absence of complaints.

Chrome is driven through playwright's `channel="chrome"`, using the browser already installed on
the machine rather than downloading a 150MB chromium.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="playwright is a dev-only dependency for the A-08 browser proof"
)

from web import app as web_app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEWPORTS = {"desktop": (1280, 800), "mobile": (390, 844)}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def base_url() -> str:
    """Serve the real app over HTTP — the page fetches its own endpoints."""
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(web_app.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:  # pragma: no cover - startup failure
            raise RuntimeError("uvicorn did not start within 20s")
        time.sleep(0.05)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        yield browser
        browser.close()


def _collect(browser, url: str, width: int, height: int, wait_for: str | None):
    """Open a page, record every console error and uncaught exception, return (page, errors)."""
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
            if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    page.goto(url, wait_until="networkidle")
    if wait_for:
        page.wait_for_selector(wait_for, timeout=15_000)
    page.wait_for_timeout(400)
    return context, page, errors


def test_the_console_counter_can_register_non_zero(browser, base_url: str) -> None:
    """The probe. Without it, every zero below is unfalsifiable."""
    context, page, errors = _collect(
        browser, base_url + "/api/scripts", 1280, 800, wait_for=None
    )
    # Raise a genuine uncaught error and a genuine console.error in the page.
    page.evaluate("() => { console.error('probe: deliberate console error'); }")
    page.evaluate("""() => {
        window.setTimeout(function () { throw new Error('probe: deliberate uncaught error'); }, 0);
    }""")
    page.wait_for_timeout(300)
    context.close()

    assert len(errors) >= 2, f"the counter failed to register deliberate errors: {errors}"
    assert any("console.error" in e for e in errors)
    assert any("pageerror" in e for e in errors)


@pytest.mark.parametrize("label", list(VIEWPORTS))
def test_page_renders_with_zero_console_errors(browser, base_url: str, label: str) -> None:
    width, height = VIEWPORTS[label]
    context, page, errors = _collect(browser, base_url, width, height, wait_for="details.video")

    # Size first: a page that rendered nothing also logs nothing.
    videos = page.query_selector_all("details.video")
    assert len(videos) == 5, f"{label}: expected 5 video rows, saw {len(videos)}"

    assert page.query_selector("#pane-catalog"), f"{label}: catalog pane missing"
    assert page.query_selector("#pane-lint"), f"{label}: lint pane missing"

    # The textarea preloads corpus/scripts/fresh_wrong.md.
    textarea = page.input_value("#lint-text")
    assert "The limit is $7,000 this year." in textarea, f"{label}: script did not preload"

    body = page.inner_text("body")
    for video_id in ("9A126b64qug", "Bzewy8DWGlo", "di6RXMzVjQQ", "6o6TiXPHlu0", "3dil-DquuG0"):
        assert video_id in body, f"{label}: {video_id} not linked on the page"

    # The honesty copy must survive any redesign.
    assert "not reproducible" in body
    assert "no sampling control" in body
    assert "has not measured that sweep" in body
    assert "has not been applied" not in body  # the retracted temperature claim

    # No horizontal overflow at either width — "mobile-readable at 390px" is a measurement.
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"{label}: page scrolls sideways by {overflow}px"

    context.close()
    assert errors == [], f"{label}: console errors: {errors}"


def test_verdict_row_shows_current_value_and_source_in_one_glance(browser, base_url: str) -> None:
    """The verdict row is the hero: the current value and its source link must both be readable."""
    context, page, errors = _collect(browser, base_url, 1280, 800, wait_for="article.v")

    row = page.query_selector("article.v")
    assert row is not None
    text = row.inner_text()
    assert "VIDEO SAID" in text.upper()
    assert "CURRENT" in text.upper()

    now = row.query_selector(".fig--now .fig__v")
    assert now and now.inner_text().strip(), "the current value is not rendered"
    link = row.query_selector(".fig--now .fig__meta a")
    assert link and link.get_attribute("href").startswith("http"), "no source link on the row"

    chip = row.query_selector(".chip")
    assert chip and chip.inner_text().strip()
    rule = row.query_selector(".rule-fired")
    assert rule and rule.inner_text().strip().startswith("R"), "the row does not show rule_fired"

    context.close()
    assert errors == []


def test_v4_row_states_what_it_read_and_flagged(browser, base_url: str) -> None:
    """The all-illustrative video must read as deliberate, not as an empty row."""
    context, page, errors = _collect(browser, base_url, 1280, 800, wait_for="details.video")

    body = page.inner_text("body")
    report = web_app._load_report()
    v4 = next(v for v in report["videos"] if "v4-" in v["source_id"])
    expected = f"{v4['read_count']} numbers read, 0 flagged ({v4['dominant_claim_type']})"
    assert expected in body, f"v4 row does not state its counts: expected {expected!r}"

    context.close()
    assert errors == []
