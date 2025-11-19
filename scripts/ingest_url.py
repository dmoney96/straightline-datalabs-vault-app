#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root (~/vault-app) is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.ingest.pipeline import ingest_source  # type: ignore[import]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch a remote PDF (or local path) and ingest into Straightline Vault."
    )
    parser.add_argument(
        "source",
        help="URL or path to ingest (e.g. https://www.irs.gov/pub/irs-pdf/p463.pdf).",
    )
    parser.add_argument(
        "--case",
        help="Optional case identifier (e.g. irs_travel, epstein_1320).",
        default=None,
    )

    args = parser.parse_args()

    try:
        pdf_path, txt_path = ingest_source(args.source, case=args.case)
    except FileNotFoundError as e:
        print(f"[ERROR] ingest failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] ingest_url failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("Ingested:")
    print(f"  source: {args.source}")
    print(f"  pdf:    {pdf_path}")
    print(f"  txt:    {txt_path}")
    print(f"  case:   {args.case or 'none'}")


if __name__ == "__main__":
    main()
