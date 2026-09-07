"""Unit tests for the A-00 pre-flight harness's real logic: .env parsing and secret masking.

The network checks (llm / fmp / youtube) are proven by running scripts/preflight.py against
live credentials, not by mocking them — a mocked API check proves nothing about the API.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from almanac.cli import load_env, mask  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("A", "B", "C", "D", "TOKEN", "ALREADY_SET"):
        monkeypatch.delenv(key, raising=False)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


def test_parses_pairs_skipping_blanks_and_comments(tmp_path):
    env = load_env(_write(tmp_path, "A=1\n\n# a comment\nB = two words\n"))
    assert env["A"] == "1"
    assert env["B"] == "two words"


def test_splits_on_first_equals_only(tmp_path):
    env = load_env(_write(tmp_path, "TOKEN=ab=cd\n"))
    assert env["TOKEN"] == "ab=cd"


def test_strips_surrounding_quotes(tmp_path):
    env = load_env(_write(tmp_path, "C=\"x\"\nD='y'\n"))
    assert env["C"] == "x"
    assert env["D"] == "y"


def test_does_not_overwrite_an_already_set_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("ALREADY_SET", "from-host")
    env = load_env(_write(tmp_path, "ALREADY_SET=from-file\n"))
    assert env["ALREADY_SET"] == "from-host"
    assert os.environ["ALREADY_SET"] == "from-host"


def test_missing_file_returns_empty_rather_than_raising(tmp_path):
    assert load_env(tmp_path / "nope.env") == {}


def test_mask_hides_the_value_and_its_tail():
    secret = "sk-ant-abcdefghij"
    masked = mask(secret)
    assert secret not in masked
    assert "abcdefghij" not in masked
    assert masked.endswith(f"(len {len(secret)})")


def test_mask_reports_absent_for_missing_values():
    assert mask(None) == "ABSENT"
    assert mask("") == "ABSENT"


def test_preflight_exits_nonzero_and_reports_absent_credentials(tmp_path):
    """With no credentials, the gate must fail honestly rather than pass vacuously."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "ALMANAC_ENV_FILE": str(tmp_path / "absent.env"),
    }
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight.py"), "--no-proof"],
        capture_output=True, text=True, cwd=ROOT, env=env,
    )
    assert proc.returncode != 0
    # Assert against the canonical PROOF line, whose format is fixed by the issue spec.
    assert "imports=PASS" in proc.stdout
    assert '"The LLM never decides" = PASS' in proc.stdout
    assert "llm=FAIL" in proc.stdout, "must fail honestly without credentials"
    assert "ABSENT" in proc.stdout
