from pathlib import Path

# Base project directory (vault-app)
BASE_DIR = Path(__file__).resolve().parents[1]

# Data directories
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
OCR_DIR = BASE_DIR / "ocr"
LOG_DIR = BASE_DIR / "logs"

# Ensure directories exist
for d in [INPUT_DIR, OUTPUT_DIR, OCR_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)
