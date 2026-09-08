"""Materialise Vertex AI credentials on a host that has no gcloud.

Locally, `_client()` authenticates through Application Default Credentials written by
`gcloud auth application-default login`. `google.auth.default()` looks in exactly four places:
the file named by `GOOGLE_APPLICATION_CREDENTIALS`, the gcloud well-known config file, App Engine,
and the GCE metadata server. A Vercel function and a GitHub Actions runner have none of them, so
every model call there fails with `DefaultCredentialsError` — not a config problem that a missing
API key would explain, but the absence of the whole ADC mechanism.

The awkward part is that `GOOGLE_APPLICATION_CREDENTIALS` must name a FILE. google-auth has no
env var for inline JSON, and a serverless platform can only hand us a string. So the string has to
become a file before the first call, which is what this module does.

Nothing here is called when real ADC is already present, so local behaviour is unchanged.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import stat
import tempfile
from pathlib import Path

ENV_INLINE_KEY = "GOOGLE_SERVICE_ACCOUNT_JSON"
ENV_CREDENTIALS_FILE = "GOOGLE_APPLICATION_CREDENTIALS"
_WRITTEN_NAME = "almanac-gcp-credentials.json"


class CredentialsUnusable(RuntimeError):
    """The supplied credential material is present but not a service-account key."""


def _decode(raw: str) -> str:
    """Accept either raw JSON or base64 of it — a key pasted into a secret arrives both ways."""
    text = raw.strip()
    if text.startswith("{"):
        return text
    try:
        return base64.b64decode(text, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise CredentialsUnusable(
            f"{ENV_INLINE_KEY} is neither JSON nor valid base64 ({exc.__class__.__name__})"
        ) from exc


def ensure_credentials(env: dict[str, str] | None = None) -> str | None:
    """Return a path google-auth can read, writing one from the environment if needed.

    Order matters and mirrors google-auth's own precedence: an explicit credentials FILE that
    exists always wins, so a runner using `google-github-actions/auth` is never second-guessed.
    Only when there is no usable file do we look for inline material.

    Returns the path in use, or None when neither is configured — in which case the caller falls
    through to ordinary ADC and fails with google-auth's own error, which names the real problem
    better than anything this module could raise.
    """
    env = os.environ if env is None else env

    existing = env.get(ENV_CREDENTIALS_FILE)
    if existing and Path(existing).is_file():
        return existing

    inline = env.get(ENV_INLINE_KEY)
    if not inline:
        return None

    payload = _decode(inline)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CredentialsUnusable(f"{ENV_INLINE_KEY} did not parse as JSON: {exc}") from exc

    kind = parsed.get("type")
    if kind != "service_account":
        # Fail on the shape rather than let google-auth fail later with a vaguer message. Never
        # echo the payload: it carries a private key.
        raise CredentialsUnusable(
            f"{ENV_INLINE_KEY} has type={kind!r}, expected 'service_account'"
        )

    target = Path(tempfile.gettempdir()) / _WRITTEN_NAME
    target.write_text(payload, encoding="utf-8")
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — the file holds a private key
    env[ENV_CREDENTIALS_FILE] = str(target)
    return str(target)
