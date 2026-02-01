from __future__ import annotations

from pathlib import Path

# Project root (the repo root)
ROOT = Path(__file__).resolve().parents[1]

# Core data dirs
INPUT_DIR = ROOT / "input"
OCR_DIR = ROOT / "ocr"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"

# Search index directory (Whoosh)
INDEX_DIR = OUTPUT_DIR / "index"

# Make sure they exist
for d in (INPUT_DIR, OCR_DIR, OUTPUT_DIR, LOG_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)
