#!/usr/bin/env python
import json
from pathlib import Path
import shutil
import sys

from vault_core.paths import DATA_DIR

MANIFEST_PATH = DATA_DIR / "manifest.jsonl"
BACKUP_PATH = DATA_DIR / "manifest.backup.jsonl"
NORMALIZED_PATH = DATA_DIR / "manifest.normalized.jsonl"


def normalize_record(rec: dict) -> dict:
    """
    Normalize a single manifest record:
      - Convert absolute PDF/TXT paths under DATA_DIR to relative.
      - Ensure URL is a string ("" instead of None).
    """
    rec = dict(rec)  # shallow copy

    pdf = rec.get("pdf")
    txt = rec.get("txt")
    url = rec.get("source_url")

    # Normalize pdf path
    if isinstance(pdf, str):
        p = Path(pdf)
        try:
            p_rel = p.relative_to(DATA_DIR)
            rec["pdf"] = str(p_rel)
        except ValueError:
            # leave as-is if not under DATA_DIR
            rec["pdf"] = pdf

    # Normalize txt path
    if isinstance(txt, str):
        t = Path(txt)
        try:
            t_rel = t.relative_to(DATA_DIR)
            rec["txt"] = str(t_rel)
        except ValueError:
            rec["txt"] = txt
    elif txt is None:
        rec["txt"] = None

    # Normalize URL
    if url is None:
        rec["source_url"] = ""
    else:
        rec["source_url"] = str(url)

    return rec


def main() -> None:
    if not MANIFEST_PATH.exists():
        print(f"[ERROR] Manifest not found at {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Backing up existing manifest to {BACKUP_PATH}")
    shutil.copy2(MANIFEST_PATH, BACKUP_PATH)

    print(f"Writing normalized manifest to {NORMALIZED_PATH}")
    with MANIFEST_PATH.open("r", encoding="utf-8") as fin, NORMALIZED_PATH.open(
        "w", encoding="utf-8"
    ) as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                print(f"[WARN] Skipping malformed line: {line[:80]!r}")
                continue

            norm = normalize_record(rec)
            fout.write(json.dumps(norm, ensure_ascii=False) + "\n")

    print("Replacing original manifest with normalized version.")
    shutil.move(str(NORMALIZED_PATH), str(MANIFEST_PATH))
    print("Done.")


if __name__ == "__main__":
    main()
