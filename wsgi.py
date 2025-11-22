#!/usr/bin/env python3
"""
WSGI entrypoint for Straightline Vault.

This file is intentionally minimal: it just locates the project
root, ensures the correct paths are on sys.path, and imports
the Flask app instance from scripts/web_app.py as `application`.
"""

import os
import sys
from pathlib import Path

# -----------------------------------------------------------
# 1. Locate project root and app directory
# -----------------------------------------------------------

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "scripts"

# Ensure project root and /scripts are on the Python path
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Optional: basic startup logging to stderr (shows in journalctl)
def _wsgi_log(msg: str) -> None:
    try:
        print(f"[WSGI] {msg}", file=sys.stderr, flush=True)
    except Exception:
        # Don't let logging issues break the app
        pass

_wsgi_log(f"Python executable: {sys.executable}")
_wsgi_log(f"Project root: {ROOT}")
_wsgi_log(f"App dir: {APP_DIR}")
_wsgi_log("Attempting to load Straightline Vault Flask app…")

# -----------------------------------------------------------
# 2. Import the actual Flask app
#    (defined as `app` in scripts/web_app.py)
# -----------------------------------------------------------

try:
    from web_app import app  # scripts/web_app.py must define `app = Flask(__name__)`
except Exception as e:
    _wsgi_log(f"FAILED TO LOAD APP: {e!r}")
    # Re-raise so Gunicorn/uWSGI fails fast and logs the traceback
    raise

# -----------------------------------------------------------
# 3. Expose WSGI callable for Gunicorn/uWSGI
# -----------------------------------------------------------

application = app

_wsgi_log("Straightline Vault app loaded successfully.")
