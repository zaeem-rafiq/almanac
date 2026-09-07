"""Offline tests for the `scan --source` switch, especially the seam A-06 plugs into.

A-06 owns `almanac/youtube.py` and is not merged. These tests stand in for it with a stub that
RECORDS how it was called, because the failure this guards against is not an exception — it is
`scan --source youtube` quietly reading the local corpus and reporting it as YouTube.
"""

from __future__ import annotations

import sys

import almanac
import types
from unittest.mock import patch

import pytest

from almanac import cli


class Args:
    source = "youtube"
    out = None
    target = None
    no_notes = True


@pytest.fixture
def stub_youtube(monkeypatch, tmp_path):
    """Install a fake almanac.youtube and capture the calls `scan` makes into it."""
    calls: list[tuple[str, dict]] = []
    module = types.ModuleType("almanac.youtube")

    def read_youtube_sources():
        calls.append(("read_youtube_sources", {}))
        return []

    def iter_sources(origin: str = "corpus", **kwargs):
        calls.append(("iter_sources", {"origin": origin}))
        return []

    module.read_youtube_sources = read_youtube_sources
    module.iter_sources = iter_sources
    monkeypatch.setitem(sys.modules, "almanac.youtube", module)
    # `from almanac.youtube import ...` resolves the ATTRIBUTE on the package, not the
    # sys.modules entry, once the real module has been imported by another test module.
    # Without this second patch the stub silently never installs and these tests assert
    # against the real youtube.py — they pass alone and fail after tests/test_youtube.py.
    monkeypatch.setattr(almanac, "youtube", module, raising=False)
    return module, calls


def run_scan(tmp_path):
    args = Args()
    args.out = str(tmp_path)
    with patch.object(cli, "_judged", return_value=({}, {})):
        return cli._cmd_scan(args)


def test_scan_youtube_prefers_the_entry_point_that_cannot_be_pointed_elsewhere(stub_youtube, tmp_path):
    module, calls = stub_youtube
    assert run_scan(tmp_path) == 0
    assert calls == [("read_youtube_sources", {})]


def test_scan_youtube_never_leaves_iter_sources_at_its_corpus_default(stub_youtube, tmp_path):
    """The bug this exists to prevent.

    A-06's `iter_sources(origin="corpus", **kwargs)` defaults to the LOCAL CORPUS. Calling it bare
    would make `scan --source youtube` scan the corpus and label the result "youtube" — and would
    make A-06's proof 3 ("youtube status counts == corpus status counts") pass by comparing the
    corpus against itself. Passing the origin explicitly is what makes that proof mean something.
    """
    module, calls = stub_youtube
    del module.read_youtube_sources
    assert run_scan(tmp_path) == 0
    assert calls == [("iter_sources", {"origin": "youtube"})]
    assert calls[0][1]["origin"] != "corpus"


def test_scan_youtube_falls_back_to_fetch_sources(stub_youtube, tmp_path):
    module, calls = stub_youtube
    del module.read_youtube_sources
    del module.iter_sources
    module.fetch_sources = lambda: calls.append(("fetch_sources", {})) or []
    assert run_scan(tmp_path) == 0
    assert calls == [("fetch_sources", {})]


def test_scan_youtube_exits_2_and_says_what_it_needs_when_a06_has_not_landed(stub_youtube, tmp_path, capsys):
    module, calls = stub_youtube
    del module.read_youtube_sources
    del module.iter_sources
    assert run_scan(tmp_path) == 2
    out = capsys.readouterr().out
    assert "read_youtube_sources" in out and "iter_sources" in out
    assert calls == []


def test_scan_corpus_does_not_touch_the_youtube_module_at_all(stub_youtube, tmp_path):
    module, calls = stub_youtube
    args = Args()
    args.source = "corpus"
    args.out = str(tmp_path)
    args.target = "corpus/channel/v4-how-id-invest-10000"
    with patch.object(cli, "_judged", return_value=({}, {})):
        assert cli._cmd_scan(args) == 0
    assert calls == []
