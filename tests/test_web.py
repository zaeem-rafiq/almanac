"""A-08: the web layer's contracts, and the write guard proved rather than asserted.

The write-guard tests here are the ones that matter. `/api/apply` is the most dangerous surface
in the project, and "we checked the flag" is not evidence. These tests instrument
`googleapiclient.discovery.build` — the chokepoint EVERY YouTube call must pass through,
including one written by a future edit that never heard of `almanac.youtube` — and count
`videos.update` invocations.

A negative assertion proves nothing on its own: a counter wired to the wrong object reads zero
exactly like a clean run. So every zero-assertion here is preceded by a probe that drives the
same counter to non-zero first.

Patching note: the stub is installed with `setattr` on the already-imported module object, never
`monkeypatch.setitem(sys.modules, ...)`. The latter does not intercept `from pkg.mod import x`
once the real module has been imported, because the package attribute wins.
"""

from __future__ import annotations

import json
from pathlib import Path

import googleapiclient.discovery
import pytest
from fastapi.testclient import TestClient

from almanac.models import Claim
from web import app as web_app

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(web_app.app)


# ------------------------------------------------------------------ the counting YouTube stub


class UpdateCounter:
    """A stand-in for the YouTube service that counts `videos().update()` and refuses to run."""

    def __init__(self) -> None:
        self.update_calls = 0
        self.other_calls: list[str] = []

    def videos(self):
        return self

    def update(self, *args, **kwargs):
        self.update_calls += 1
        return self

    def list(self, *args, **kwargs):
        self.other_calls.append("videos.list")
        return self

    def execute(self, *args, **kwargs):
        return {"items": []}


@pytest.fixture()
def counter(monkeypatch: pytest.MonkeyPatch) -> UpdateCounter:
    """Install the counting stub at the chokepoint any YouTube write must pass through."""
    stub = UpdateCounter()
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: stub)

    # Belt and braces: `almanac.youtube` is read-only today, but if a future edit reached for its
    # client builder instead of `discovery.build`, this catches that path too.
    youtube = pytest.importorskip("almanac.youtube")
    monkeypatch.setattr(youtube, "build_client", lambda *a, **k: stub, raising=False)
    return stub


# --------------------------------------------------------------------------- GET / and report


def test_index_serves_the_page(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Assert the SIZE of what we check: an empty 200 must not pass for "the page renders".
    assert len(response.text) > 5_000
    assert "Catalog watch" in response.text
    assert "Lint a script" in response.text


def test_report_has_five_videos_each_matching_the_corpus(client: TestClient) -> None:
    """Assert the count on BOTH sides — "0 videos, 0 errors" passes a sloppy check."""
    corpus_dirs = sorted(p for p in (REPO_ROOT / "corpus" / "channel").iterdir() if p.is_dir())
    assert len(corpus_dirs) == 5, "the corpus itself must hold 5 videos"

    response = client.get("/api/report")
    assert response.status_code == 200, response.text
    report = response.json()
    assert len(report["videos"]) == 5

    for video in report["videos"]:
        assert video["video_id"], f"{video['source_id']} has no video_id to link to"
        assert video["watch_url"].startswith("https://www.youtube.com/watch?v=")
        assert video["verdicts"], f"{video['source_id']} produced no verdicts"
        assert video["read_count"] == len(video["verdicts"])
        for row in video["verdicts"]:
            assert row["rule_fired"], "every verdict must carry the rule that produced it"


def test_report_never_starts_a_scan(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The page reads a build artifact. A scan on page-view would bill every visitor."""
    import almanac.extract

    def explode(*args, **kwargs):  # pragma: no cover - the assertion is that this never runs
        raise AssertionError("/api/report called the extractor")

    monkeypatch.setattr(almanac.extract, "extract_sources", explode)
    assert client.get("/api/report").status_code == 200


def test_report_says_so_plainly_when_the_scan_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(web_app, "REPORT_PATH", tmp_path / "latest.json")
    response = client.get("/api/report")
    assert response.status_code == 503
    assert "almanac scan" in response.json()["detail"]


def test_v4_count_line_is_read_from_the_run_not_hardcoded(client: TestClient) -> None:
    """ADR-002 D-5: extraction is not reproducible, so a fixed count would be a fabricated figure."""
    report = client.get("/api/report").json()
    v4 = next(v for v in report["videos"] if "v4-" in v["source_id"])
    assert v4["flagged_count"] == 0
    assert v4["read_count"] == len(v4["verdicts"])
    assert v4["dominant_claim_type"] == "illustrative"
    assert all(row["status"] == "skip" for row in v4["verdicts"])


def test_page_quotes_only_measured_figures(client: TestClient) -> None:
    """Nothing on this page may claim more than the eval table supports.

    This test used to hard-code "89% (48/54)" — a figure measured on a model the project no longer
    runs. A literal here re-creates exactly the drift the page promises not to have: the number
    stays put while the run moves. It now compares the page against evals/results.json, so the
    assertion cannot outlive the measurement.
    """
    import json
    from pathlib import Path as _P

    raw = json.loads((_P(__file__).resolve().parents[1] / "evals" / "results.json").read_text())
    by_status = {row["status"]: row for row in raw["status_scores"]}

    measured = client.get("/api/report").json()["measured"]

    expected_recall = f"{round(raw['extraction_recall'] * 100)}% ({raw['run']['matched']}/{raw['run']['labelled_rows']})"
    assert measured["extraction_recall"] == expected_recall
    assert measured["claim_type_accuracy"] == f"{round(raw['claim_type']['accuracy'] * 100)}%"
    assert measured["stale_material_recall"] == f"{by_status['stale_material']['recall']:.2f}"
    assert measured["skip_recall"] == f"{by_status['skip']['recall']:.2f}"

    # Reproducibility is measured now, not assumed, so the page is checked against the sampling
    # artifact rather than against a literal. This assertion used to read
    # `measured["extraction_reproducible"] is False`, which was true of claude-opus-5 (verdict
    # counts moved 65-84 run to run, gate failed 2 in 5) and is false of the engine that ships:
    # six consecutive live runs produced byte-identical scored output. Pinning the old value would
    # force the page to keep understating a result it can now evidence.
    repro = json.loads((_P(__file__).resolve().parents[1] / "evals" / "reproducibility.json").read_text())
    assert measured["extraction_repeats_identically"] is repro["identical_across_runs"]
    assert str(repro["gate_passes"]) in measured["reproducibility"]
    assert str(repro["gate_runs"]) in measured["reproducibility"]
    assert measured["reproducibility_scope"] == repro["scope_of_the_claim"]

    # The scope caveat is the load-bearing half of the claim: a pinned sample is still a sample.
    assert "not a vendor guarantee" in measured["reproducibility_scope"]

    # The defect statement must name what is actually enforced, not a sampling knob. ADR-000 §10:
    # claude-opus-5 accepted no sampling control at all, and the engine has since moved to Gemini
    # on Vertex — so `temperature` was never the cause on either engine.
    assert "coverage sweep" in measured["known_defect"]

    # The figures now come from a post-sweep run on the shipping engine, so the page no longer
    # carries the "measured before the sweep" caveat.
    assert measured["figures_predate_the_sweep"] is False
    assert measured["sweep_measured_here"] is True

    page = client.get("/").text

    # The panel must still disclose the engine's real defect and refuse to pool the retired
    # engine's sample. "not reproducible" is deliberately NOT asserted any more -- it was the old
    # copy's headline and the measurement now contradicts it.
    assert "over-reads" in page
    assert "038dbe6" in page          # the retired engine is named, not quietly dropped
    assert "not pooled" in page

    # Regression guard on the retracted claim. It was false twice over — the parameter does not
    # exist on the model it named, and a different fix already landed — and it sat in the panel the
    # page's credibility rests on, so it must not come back.
    # These three pin the RETRACTED claims themselves. A blanket ban on the word "temperature"
    # used to stand here; it was a crude proxy and it now blocks true copy -- the scope caveat
    # correctly says the vendor does not promise determinism for a pinned temperature and seed.
    # Ban the false statements, not the vocabulary.
    assert "has not been applied" not in page
    assert "API default of 1.0" not in page
    assert "no sampling control" not in page


# ---------------------------------------------------------------------------------- /api/lint


def _fake_claim(**kwargs) -> Claim:
    base = dict(
        source_id="(pasted script)", locator="L1", quote="The limit is $7,000 this year.",
        claim_type="statutory_limit", entity_key="ira_contribution", value=7000.0,
        unit="usd", year_hint=None, confidence=0.9,
    )
    base.update(kwargs)
    return Claim(**base)


def test_lint_returns_rows_carrying_rule_fired(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stubbed model, real judge: the verdict is still decided by judge.py rules."""
    import almanac.extract
    import almanac.notes

    monkeypatch.setattr(
        almanac.extract, "extract_sources",
        lambda sources, **kw: {sources[0].source_id: [
            _fake_claim(),
            _fake_claim(locator="L2", claim_type="illustrative", entity_key=None, value=10.0),
        ]},
    )
    monkeypatch.setattr(almanac.notes, "annotate", lambda verdicts, **kw: {})

    response = client.post("/api/lint", json={"text": "The limit is $7,000 this year."})
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["verdicts"]) == 2
    assert body["counts"]["stale_material"] == 1
    assert body["counts"]["skip"] == 1
    assert isinstance(body["elapsed_ms"], int)

    stale = next(r for r in body["verdicts"] if r["status"] == "stale_material")
    assert stale["rule_fired"] == "R5.stale_material"
    assert stale["current_value"] == 7500.0
    assert stale["source_url"]


def test_lint_rejects_an_empty_body(client: TestClient) -> None:
    assert client.post("/api/lint", json={"text": "   "}).status_code == 422


def test_lint_enforces_the_eight_thousand_character_cap(client: TestClient) -> None:
    assert web_app.MAX_LINT_CHARS == 8_000
    response = client.post("/api/lint", json={"text": "x" * (web_app.MAX_LINT_CHARS + 1)})
    assert response.status_code == 413
    assert "8,000" in response.json()["detail"]


def test_scripts_endpoint_serves_the_real_corpus_files(client: TestClient) -> None:
    body = client.get("/api/scripts").json()["scripts"]
    for name, filename in web_app.CORPUS_SCRIPTS.items():
        on_disk = (REPO_ROOT / "corpus" / "scripts" / filename).read_text(encoding="utf-8")
        assert body[name]["text"] == on_disk, f"{name} drifted from corpus/scripts/{filename}"


# ------------------------------------------------------- /api/apply — the write guard, proved


def test_the_update_counter_can_register_non_zero(counter: UpdateCounter) -> None:
    """The probe. A counter that cannot count makes every zero below meaningless."""
    service = googleapiclient.discovery.build("youtube", "v3")
    service.videos().update(part="snippet", body={}).execute()
    assert counter.update_calls == 1


def test_apply_returns_a_diff_and_calls_no_update(
    client: TestClient, counter: UpdateCounter
) -> None:
    assert counter.update_calls == 0

    response = client.post("/api/apply", json={})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["dry_run"] is True
    assert body["wrote"] is False
    assert body["changed_count"] >= 1, "the corpus has stale videos; a diff should be produced"

    changed = [c for c in body["changes"] if c["changed"]]
    for change in changed:
        assert change["diff"].strip(), "a changed description must come with a diff"
        assert change["note_count"] >= 1
        # The real invariant: the composed description carries the drafted notes verbatim.
        # Not "every note starts with the pin" — `notes.check_note()` does not enforce that, and
        # a test may only assert what the code guarantees.
        for note in change["notes"]:
            assert note in change["proposed_description"]
        assert change["proposed_description"] != change["current_description"]

    assert counter.update_calls == 0, "/api/apply called videos.update"


@pytest.mark.parametrize("write_flag", ["true", "TRUE", "1", "false", None])
def test_apply_never_writes_whatever_almanac_write_says(
    client: TestClient, counter: UpdateCounter, monkeypatch: pytest.MonkeyPatch, write_flag
) -> None:
    """Dry-run UNCONDITIONALLY. A web request cannot supply the `--apply` the rules require,
    so no environment flag may turn this endpoint into a writer."""
    if write_flag is None:
        monkeypatch.delenv("ALMANAC_WRITE", raising=False)
    else:
        monkeypatch.setenv("ALMANAC_WRITE", write_flag)

    body = client.post("/api/apply", json={}).json()
    assert body["dry_run"] is True
    assert body["wrote"] is False
    assert body["write_attempted"] is False
    assert counter.update_calls == 0


def test_apply_can_target_one_video(client: TestClient, counter: UpdateCounter) -> None:
    report = client.get("/api/report").json()
    target = next(v for v in report["videos"] if v["flagged_count"] > 0)

    body = client.post("/api/apply", json={"source_id": target["source_id"]}).json()
    assert len(body["changes"]) == 1
    assert body["changes"][0]["source_id"] == target["source_id"]
    assert body["changes"][0]["video_id"] == target["video_id"]
    assert counter.update_calls == 0


def test_apply_404s_on_an_unknown_source(client: TestClient) -> None:
    assert client.post("/api/apply", json={"source_id": "nope/nope.srt"}).status_code == 404


def test_apply_composes_from_notes_the_judge_already_ruled(client: TestClient) -> None:
    """The diff carries only notes attached to `stale_material` rows. It invents nothing."""
    report = client.get("/api/report").json()
    for video in report["videos"]:
        expected = [
            row["note"] for row in video["verdicts"]
            if row["status"] == "stale_material" and row.get("note")
        ]
        change = web_app.compose_description(video)
        assert change["notes"] == expected
        for note in change["notes"]:
            assert note in change["proposed_description"]


def test_the_web_layer_holds_no_youtube_write_call() -> None:
    """Static backstop to the runtime probe: no update call may appear in this module."""
    source = (REPO_ROOT / "web" / "app.py").read_text(encoding="utf-8")
    assert ".update(" not in source
    assert "videos()" not in source


def test_apply_payload_is_json_serialisable(client: TestClient) -> None:
    json.loads(client.post("/api/apply", json={}).text)
