"""Almanac command-line entry point and configuration loading.

RULE (Project Rules, "Secrets"): secrets come only from `.env` locally and from GitHub Actions
secrets / host env in the cloud. Never print a secret; print a prefix check only.

`python-dotenv` is deliberately not used — it is not in the approved package list and new
dependencies need Zaeem's approval (ADR-000 gap G-5). Hence the small reader below.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"


def load_env(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Read `.env` into the process environment and return the effective values.

    Rules:
      * a missing file is not an error — returns whatever is already in `os.environ`
      * blank lines and `#` comments are skipped
      * a line splits on its FIRST `=` only, so values may contain `=`
      * surrounding single or double quotes are stripped
      * a variable already set in `os.environ` is NOT overwritten, so CI secrets and host env
        always win over a file on disk
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV_PATH
    parsed: dict[str, str] = {}

    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            parsed[key] = value

    for key, value in parsed.items():
        os.environ.setdefault(key, value)

    return {key: os.environ[key] for key in parsed if key in os.environ}


def mask(value: str | None, keep: int = 6) -> str:
    """Render a secret safely: a short leading prefix and a length. Never the tail.

    Returns "ABSENT" for a missing or empty value so preflight can name what is unset without
    ever emitting the value itself.
    """
    if not value:
        return "ABSENT"
    if len(value) <= keep:
        return f"…(len {len(value)})"
    return f"{value[:keep]}…(len {len(value)})"


def main(argv: list[str] | None = None) -> int:
    """A-03+ fills this in: `rates --refresh`, `scan`, `report`, `apply`."""
    raise SystemExit("almanac CLI is not implemented yet (scaffolded in A-00).")
