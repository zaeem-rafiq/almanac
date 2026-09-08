"""A-09: the deploy config, guarded — it broke the live site once and gave no error doing it.

The first production deploy returned FastAPI's own `{"detail":"Not Found"}` on every route. Nothing
failed: `vercel deploy --prod` reported Ready, and `vercel inspect` showed why — a single lambda
named `fastapi` built from `.`, not from `api/index.py`. Vercel's FastAPI framework preset had been
auto-detected during `vercel link` and silently overrode both `functions` and the entrypoint.

A wrong value here produces a green deploy serving nothing, so it is worth asserting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parents[1] / "vercel.json"


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_framework_detection_is_disabled(config: dict):
    """`null` means the "Other" preset. Absent means Vercel guesses, and its guess was wrong."""
    assert "framework" in config, "framework key absent — Vercel will auto-detect and override"
    assert config["framework"] is None


def test_the_function_is_our_entrypoint(config: dict):
    fn = config["functions"]["api/index.py"]
    assert fn["maxDuration"] == 60
    assert (Path(__file__).resolve().parents[1] / "api" / "index.py").is_file()


def test_every_directory_the_app_reads_at_import_is_bundled(config: dict):
    """evals/ is the one that bit us: web/app.py builds MEASURED from it at import time, so
    omitting it raised FileNotFoundError before any route ran."""
    included = config["functions"]["api/index.py"]["includeFiles"]
    for needed in ("almanac", "evals", "facts", "corpus", "reports", "web"):
        assert needed in included, f"{needed}/ missing from includeFiles"


def test_a_catch_all_rewrite_reaches_the_function(config: dict):
    rewrites = config["rewrites"]
    assert any(r["source"] == "/(.*)" and r["destination"] == "/api/index" for r in rewrites)


def test_the_bundled_dirs_are_not_excluded_from_the_upload():
    """.vercelignore wins over includeFiles: a directory excluded there never reaches the host,
    so includeFiles cannot resurrect it. These two files have to agree."""
    ignore = (Path(__file__).resolve().parents[1] / ".vercelignore").read_text(encoding="utf-8")
    excluded = {ln.strip().rstrip("/") for ln in ignore.splitlines()
                if ln.strip() and not ln.startswith("#")}
    for needed in ("almanac", "evals", "facts", "corpus", "reports", "web"):
        assert needed not in excluded, f"{needed}/ is in .vercelignore but includeFiles needs it"
    # control: the file must actually be excluding things, or this assertion proves nothing
    assert {"tests", "docs", "venv"} & excluded, ".vercelignore excludes nothing — check parsing"
