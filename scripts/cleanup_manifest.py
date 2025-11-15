from __future__ import annotations

import json
import sys
from pathlib import Path

# --- Make project root importable so "vault_core" works ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.manifest import MANIFEST_PATH  # now this import should succeed


def is_good_record(rec: dict) -> bool:
    """
    Decide whether a manifest record is worth keeping.

    Right now we:
      - require a dict
      - drop entries where *both* pdf and txt are missing
      - drop entries where everything is basically None

    You can tighten this later if you want.
    """
    if not isinstance(rec, dict):
        return False

    pdf = rec.get("pdf")
    txt = rec.get("txt")
    source_url = rec.get("source_url")
    kind = rec.get("kind")

    # If it has neither pdf nor txt, it's probably not useful
    if not pdf and not txt:
        return False

    # If everything is None/empty, also drop
    if not any([pdf, txt, source_url, kind]):
        return False

    return True


def main() -> None:
    if not MANIFEST_PATH.exists():
        print(f"No manifest file at {MANIFEST_PATH}, nothing to clean.")
        return

    # Backup first
    backup_path = MANIFEST_PATH.with_suffix(".jsonl.bak")
    backup_path.write_text(MANIFEST_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Backed up original manifest to {backup_path}")

    kept = []
    dropped = 0

    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                dropped += 1
                continue

            if is_good_record(rec):
                kept.append(rec)
            else:
                dropped += 1

    with MANIFEST_PATH.open("w", encoding="utf-8") as f:
        for rec in kept:
            json.dump(rec, f, ensure_ascii=False)
            f.write("\n")

    print("Cleanup complete.")
    print(f"  Kept:    {len(kept)} records")
    print(f"  Dropped: {dropped} records")
    print(f"  Manifest: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
