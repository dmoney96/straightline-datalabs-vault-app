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


def iter_sources_from_file(path: Path):
    """
    Yield non-empty, non-comment lines from a text file.
    Lines starting with '#' are ignored.
    """
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            yield line


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-ingest a list of URLs and/or file paths into Straightline Vault."
    )
    parser.add_argument(
        "list_file",
        help="Path to a text file containing one URL or file path per line.",
    )
    parser.add_argument(
        "--case",
        help="Optional case identifier applied to all ingested items (e.g. epstein_1320, irs_travel).",
        default=None,
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop immediately on first error instead of continuing.",
    )

    args = parser.parse_args()

    list_path = Path(args.list_file)
    if not list_path.exists():
        print(f"[ERROR] list file does not exist: {list_path}", file=sys.stderr)
        raise SystemExit(1)

    total = 0
    successes = 0
    failures = 0

    print(f"[INFO] Starting bulk ingest from {list_path} (case={args.case or 'none'})")

    for source in iter_sources_from_file(list_path):
        total += 1
        print(f"\n[{total}] Ingesting: {source!r}")

        try:
            pdf_path, txt_path = ingest_source(source, case=args.case)
        except FileNotFoundError as e:
            failures += 1
            print(f"[ERROR] File not found for {source!r}: {e}", file=sys.stderr)
            if args.stop_on_error:
                break
            continue
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"[ERROR] Unexpected ingest error for {source!r}: {e}", file=sys.stderr)
            if args.stop_on_error:
                break
            continue

        successes += 1
        print("  -> Ingested:")
        print(f"       pdf: {pdf_path}")
        print(f"       txt: {txt_path}")

    print("\n[SUMMARY]")
    print(f"  Total:     {total}")
    print(f"  Successes: {successes}")
    print(f"  Failures:  {failures}")

    if failures > 0:
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
