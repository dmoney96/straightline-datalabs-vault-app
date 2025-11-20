#!/usr/bin/env python
from __future__ import annotations

import argparse
from typing import Optional

from vault_core.search_backend import run_search  # type: ignore[import]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search Straightline Vault index from the command line."
    )
    parser.add_argument(
        "query",
        nargs="+",
        help="Search terms (e.g. 'massage table' or 'flight logs').",
    )
    parser.add_argument(
        "--case",
        help="Filter results to a specific case name (e.g. maxwell_1320, irs_travel).",
        default=None,
    )
    parser.add_argument(
        "--kind",
        help="Filter results by manifest kind (e.g. local_file, url_fetch).",
        default=None,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of matching results to display (after filtering).",
    )

    args = parser.parse_args()
    query_text = " ".join(args.query)

    print(f"🔎 Searching for: {query_text!r}")
    if args.case:
        print(f"   (case filter: {args.case})")
    if args.kind:
        print(f"   (kind filter: {args.kind})")
    print()

    results = run_search(
        query_text=query_text,
        case=args.case or None,
        kind=args.kind or None,
        limit=args.limit,
    )

    if not results:
        print("No results found.")
        return

    for r in results:
        meta_bits = []
        if r["case"]:
            meta_bits.append(f"case={r['case']}")
        if r["kind"]:
            meta_bits.append(f"kind={r['kind']}")
        meta_str = f" [{' '.join(meta_bits)}]" if meta_bits else ""

        print(f"📄 {r['doc_id']}  (score={r['score']:.2f}){meta_str}")
        print(f"    Source: {r['source']}")
        print("-" * 80)
        print(r["snippet"])
        print()


if __name__ == "__main__":
    main()
