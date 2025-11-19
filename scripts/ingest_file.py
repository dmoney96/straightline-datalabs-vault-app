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


def normalize_pdf_arg(arg: str) -> str:
    """
    Normalize the CLI path a bit so we don't end up with input/input/... mistakes.

    Rules:
      - If arg is absolute: use it as-is.
      - If arg starts with 'input/' or './input/': strip the leading 'input/' portion,
        because ingest_source will prepend INPUT_DIR for relative paths.
      - Otherwise, return the arg unchanged (relative to INPUT_DIR).
    """
    p = Path(arg)

    if p.is_absolute():
        return str(p)

    parts = p.parts
    if parts and parts[0] in ("input", "./input"):
        # Drop the leading 'input' so 'input/p463.pdf' becomes 'p463.pdf'
        p = Path(*parts[1:])

    return str(p)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest a single local PDF into Straightline Vault."
    )
    parser.add_argument(
        "pdf",
        help="PDF file to ingest. Can be a filename relative to input/ or an absolute path.",
    )
    parser.add_argument(
        "--case",
        help="Optional case identifier (e.g. maxwell_1320, irs_travel).",
        default=None,
    )

    args = parser.parse_args()

    # Normalize for ingest pipeline
    source = normalize_pdf_arg(args.pdf)

    try:
        pdf_path, txt_path = ingest_source(source, case=args.case)
    except FileNotFoundError as e:
        print(f"[ERROR] ingest failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] unexpected ingest error: {e}", file=sys.stderr)
        sys.exit(1)

    print("Ingested:")
    print(f"  pdf:  {pdf_path}")
    print(f"  txt:  {txt_path}")
    print(f"  case: {args.case or 'none'}")


if __name__ == "__main__":
    main()
