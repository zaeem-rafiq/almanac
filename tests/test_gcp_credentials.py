"""A-09: the Vertex credential bootstrap for hosts with no gcloud.

These matter more than most: the material this module handles is a private key, and the failure
mode it exists to prevent (DefaultCredentialsError at request time on a live host) is invisible
until someone loads the page.
"""

from __future__ import annotations

import base64
import json
import stat
from pathlib import Path

import pytest

from almanac.gcp_credentials import (
    ENV_CREDENTIALS_FILE,
    ENV_INLINE_KEY,
    CredentialsUnusable,
    ensure_credentials,
)

KEY = {"type": "service_account", "project_id": "polygraph-hackathon",
       "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
       "client_email": "almanac@polygraph-hackathon.iam.gserviceaccount.com"}


def test_no_configuration_returns_none_and_leaves_adc_alone():
    """Locally there is real ADC and this module must not interfere with it."""
    env: dict[str, str] = {}
    assert ensure_credentials(env) is None
    assert env == {}


def test_an_existing_credentials_file_wins(tmp_path: Path):
    """A runner using google-github-actions/auth already has a file; do not second-guess it."""
    existing = tmp_path / "from-the-action.json"
    existing.write_text(json.dumps(KEY), encoding="utf-8")
    env = {ENV_CREDENTIALS_FILE: str(existing), ENV_INLINE_KEY: base64.b64encode(b"{}").decode()}

    assert ensure_credentials(env) == str(existing)
    assert env[ENV_CREDENTIALS_FILE] == str(existing)


def test_raw_json_is_written_to_a_file_and_pointed_at():
    """google-auth reads a PATH; it has no env var for inline JSON. That is the whole point."""
    env = {ENV_INLINE_KEY: json.dumps(KEY)}
    path = ensure_credentials(env)

    assert path is not None
    assert env[ENV_CREDENTIALS_FILE] == path
    assert json.loads(Path(path).read_text(encoding="utf-8")) == KEY


def test_base64_is_accepted_because_that_is_how_a_secret_arrives():
    env = {ENV_INLINE_KEY: base64.b64encode(json.dumps(KEY).encode()).decode()}
    path = ensure_credentials(env)

    assert path is not None
    assert json.loads(Path(path).read_text(encoding="utf-8")) == KEY


def test_the_written_key_is_not_world_readable():
    """It is a private key on a shared temp dir."""
    env = {ENV_INLINE_KEY: json.dumps(KEY)}
    path = ensure_credentials(env)

    mode = Path(path).stat().st_mode
    assert not mode & stat.S_IRGRP, "group can read the private key"
    assert not mode & stat.S_IROTH, "world can read the private key"


def test_a_non_service_account_key_is_refused_by_shape():
    """An OAuth client secret pasted in by mistake must fail here, not deep inside google-auth."""
    env = {ENV_INLINE_KEY: json.dumps({"type": "authorized_user", "client_id": "x"})}
    with pytest.raises(CredentialsUnusable, match="expected 'service_account'"):
        ensure_credentials(env)


def test_garbage_is_refused_and_the_payload_is_never_echoed():
    env = {ENV_INLINE_KEY: "this is neither json nor base64 !!!"}
    with pytest.raises(CredentialsUnusable) as caught:
        ensure_credentials(env)
    assert "neither json nor valid base64" in str(caught.value).lower()
    assert "this is neither" not in str(caught.value), "the raw value leaked into the error"
