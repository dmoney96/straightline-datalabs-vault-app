#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.ingest import fetch_pdf


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/fetch.py <url>")
        raise SystemExit(1)

    url = sys.argv[1]
    out = fetch_pdf(url)
    print(f"Downloaded → {out}")


if __name__ == "__main__":
    main()
