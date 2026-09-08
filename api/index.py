"""A-09: Vercel Python entrypoint. Deploy config only — no application logic lives here.

The Vercel Python runtime looks for a module-level ASGI app named `app`. This file re-exports
A-08's FastAPI app unchanged.

`web/` has no `__init__.py`, so `web.app` resolves as a PEP 420 namespace package. That only
works when the repository root is on `sys.path`, which the runtime does not guarantee for a
function whose entrypoint sits in `api/`. The insertion below makes it explicit rather than
relying on the runtime's cwd.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from web.app import app  # noqa: E402  (path setup must precede the import)

__all__ = ["app"]
