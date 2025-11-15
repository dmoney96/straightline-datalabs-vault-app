# Straightline Datalabs Vault – Architecture & Roadmap

_Last updated: 2025-11-15_

## 1. High-level goal

A **people’s research platform** designed for:

- Journalists, researchers, watchdogs, and curious citizens
- Pulling in **primary sources** (public PDFs, docs, etc.)
- Turning them into a **searchable, high-signal corpus** with:
  - Strong provenance (where did this come from?)
  - Ethics baked in (chain-of-custody, auditability, resistance to abuse)
  - Tools that make “being careful” the default, not an extra chore

Think: **“Google, if it had grown a conscience and updated its tooling for 2025.”**

---

## 2. Current components (MVP core)

### 2.1 Data directories

Rooted at the repo:

- `input/` – raw documents (e.g., downloaded PDFs)
- `ocr/` – OCR’d text (`.txt` per input file)
- `output/`
  - `output/index/` – Whoosh search index
- `logs/` – space for future audit / ingestion logs
- `data/` – misc structured data (future: configs, dictionaries, rulesets)

These paths are centralized in:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INPUT_DIR = ROOT / "input"
OCR_DIR = ROOT / "ocr"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"

for d in (INPUT_DIR, OCR_DIR, OUTPUT_DIR, LOG_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)
