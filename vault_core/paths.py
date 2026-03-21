import os
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# Code base (read-only)
# ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]  # vault-app/

# ─────────────────────────────────────────────────────────────
# Corpus/data (where PDFs/TXTs/manifest/index actually live)
# ─────────────────────────────────────────────────────────────
DATA_ROOT = Path(os.getenv("STRAIGHTLINE_DATA_DIR", "/opt/straightline-vault")).resolve()

if not DATA_ROOT.exists():
    raise RuntimeError(f"DATA_ROOT missing: {DATA_ROOT} (set STRAIGHTLINE_DATA_DIR)")

DATA_DIR = DATA_ROOT  # backward compat

# These live with the corpus
OCR_DIR = DATA_ROOT / "ocr"
WEB_PDFS_DIR = DATA_ROOT / "web_pdfs"
PROVENANCE_DIR = DATA_ROOT / "provenance"
MANIFEST_PATH = DATA_ROOT / "manifest.jsonl"  # if you reference it elsewhere

# Whoosh index: IMPORTANT — your real index is data/index, not output/index
INDEX_DIR = Path(os.getenv("STRAIGHTLINE_INDEX_DIR", str(DATA_ROOT / "index"))).resolve()

# ─────────────────────────────────────────────────────────────
# Runtime (system-managed: jobs/logs/db) — can stay in /opt
# ─────────────────────────────────────────────────────────────
RUNTIME_ROOT = Path(os.getenv("STRAIGHTLINE_RUNTIME_DIR", "/opt/straightline-vault")).resolve()

INPUT_DIR  = RUNTIME_ROOT / "input"
OUTPUT_DIR = RUNTIME_ROOT / "output"
LOG_DIR    = RUNTIME_ROOT / "logs"
JOBS_DIR   = RUNTIME_ROOT / "jobs"
DB_DIR     = RUNTIME_ROOT / "db"

for d in (OCR_DIR, WEB_PDFS_DIR, PROVENANCE_DIR, INDEX_DIR, INPUT_DIR, OUTPUT_DIR, LOG_DIR, JOBS_DIR, DB_DIR):
    d.mkdir(parents=True, exist_ok=True)
