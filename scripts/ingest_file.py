#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path so "vault_core" imports work
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.ingest.pipeline import ingest_source
from vault_core.manifest import iter_manifest


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/ingest_file.py /path/to/file.pdf")
        raise SystemExit(1)

    src = sys.argv[1]
    pdf_path, txt_path = ingest_source(src)

    print("Ingested:")
    print(f"  pdf: {pdf_path}")
    print(f"  txt: {txt_path}")

    # Show a tiny tail of the manifest so you can see what got recorded
    entries = list(iter_manifest())
    print("\nLast few manifest entries:")
    for rec in entries[-5:]:
        source = rec.get("source_url") or rec.get("source_path")
        print(
            f"  - {rec.get('kind')} {source} -> "
            f"{rec.get('pdf')} / {rec.get('txt')}"
        )


if __name__ == "__main__":
    main()
