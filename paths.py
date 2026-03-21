from pathlib import Path

# ─────────────────────────────────────────────────────────────
# Code base (read-only)
# ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]  # vault-app/

# ─────────────────────────────────────────────────────────────
# Runtime data (read/write, system-managed)
# ─────────────────────────────────────────────────────────────
DATA_ROOT = Path("/opt/straightline-vault")

if not DATA_ROOT.exists():
    raise RuntimeError("DATA_ROOT missing; service misconfigured")

INPUT_DIR  = DATA_ROOT / "input"
OUTPUT_DIR = DATA_ROOT / "output"
OCR_DIR    = DATA_ROOT / "ocr"
LOG_DIR    = DATA_ROOT / "logs"
JOBS_DIR   = DATA_ROOT / "jobs"
DB_DIR     = DATA_ROOT / "db"

# Search index directory (Whoosh)
INDEX_DIR = OUTPUT_DIR / "index"

# Backward compatibility (required by manifest.py)
DATA_DIR = DB_DIR

# Ensure runtime dirs exist
for d in (
    INPUT_DIR,
    OUTPUT_DIR,
    OCR_DIR,
    LOG_DIR,
    JOBS_DIR,
    DB_DIR,
    INDEX_DIR,
):
    d.mkdir(parents=True, exist_ok=True)
