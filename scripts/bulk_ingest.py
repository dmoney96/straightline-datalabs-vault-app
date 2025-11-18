#!/usr/bin/env python

import argparse
import sys
from pathlib import Path

# Ensure project root (~/vault-app) is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.ingest.pipeline import ingest_source  # type: ignore[import]


def iter_pdfs(root: Path):
    """Yield all .pdf files under root, sorted."""
    for path in sorted(root.rglob("*.pdf")):
        yield path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-ingest a directory of PDFs into Straightline Vault."
    )
    parser.add_argument(
        "directory",
        help="Directory containing PDFs (e.g. input/1320_pages).",
    )
    parser.add_argument(
        "--case",
        help="Optional case identifier applied to all ingested files.",
        default=None,
    )

    args = parser.parse_args()

    root = Path(args.directory).resolve()
    if not root.exists() or not root.is_dir():
        print(f"[ERROR] {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    total = 0
    ok = 0
    failed = 0

    for pdf in iter_pdfs(root):
        total += 1
        rel = pdf.relative_to(root)
        print(f"[INFO] Ingesting {rel} ...")
        try:
            ingest_source(str(pdf), case=args.case)
            ok += 1
        except FileNotFoundError as e:
            print(f"[ERROR] {rel}: {e}", file=sys.stderr)
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] {rel}: unexpected error: {e}", file=sys.stderr)
            failed += 1

    print()
    print(f"[SUMMARY] processed={total} ok={ok} failed={failed}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
