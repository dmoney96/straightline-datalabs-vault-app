#!/usr/bin/env python
from __future__ import annotations

"""
WSGI entrypoint for Straightline Vault.

Gunicorn runs:  wsgi:application
"""

import sys
from pathlib import Path

# Project root, e.g. /home/dom/vault-app
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.paths import DATA_ROOT, INDEX_DIR, RUNTIME_ROOT
print("WSGI DATA_ROOT:", DATA_ROOT)
print("WSGI INDEX_DIR:", INDEX_DIR)
print("WSGI RUNTIME_ROOT:", RUNTIME_ROOT)

# Also add scripts/ so we can import scripts.web_app
SCRIPTS_DIR = ROOT / "scripts"
if SCRIPTS_DIR.is_dir() and str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Debug breadcrumbs – these will show up in journalctl when Gunicorn imports wsgi
print("WSGI ROOT:", ROOT)
print("WSGI SCRIPTS_DIR:", SCRIPTS_DIR)
print("WSGI scripts/web_app.py exists?:", (SCRIPTS_DIR / "web_app.py").exists())
print("WSGI sys.path[0:5]:", sys.path[0:5])

# Import the Flask app object from scripts/web_app.py
from scripts.web_app import app as application  # type: ignore[import]
