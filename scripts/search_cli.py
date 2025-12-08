#!/usr/bin/env python
from __future__ import annotations

import argparse
from typing import Any, Dict, List, Optional

from vault_core.search_backend import run_search  # type: ignore[import]


def _coerce_hit(hit: Any) -> Dict[str, Any]:
    """
    Normalize different possible hit types (Whoosh Hit, dataclass, dict)
    into a simple dict with keys: doc_id, score, snippet, source, case, kind.
    """
    out: Dict[str, Any] = {
        "doc_id": None,
        "score": None,
        "snippet": None,
        "source": None,
        "case": None,
        "kind": None,
    }

    # --- Try attributes first ---
    doc_id = getattr(hit, "doc_id", None) or getattr(hit, "id", None)
    score = getattr(hit, "score", None)
    snippet = getattr(hit, "snippet", None)
    source = getattr(hit, "source", None)

    # If this is a Whoosh Hit, pull fields() as well
    fields: Dict[str, Any] = {}
    if hasattr(hit, "fields") and callable(getattr(hit, "fields")):
        try:
            fields = hit.fields()
        except Exception:
            fields = {}

    # --- Dict-style access, if applicable ---
    if isinstance(hit, dict):
        fields = {**fields, **hit}

    # Fill out common fields
    out["doc_id"] = doc_id or fields.get("doc_id") or fields.get("id")
    out["score"] = score if score is not None else fields.get("score")
    out["snippet"] = snippet or fields.get("snippet")

    out["source"] = (
        source
        or fields.get("source")
        or fields.get("source_url")
        or fields.get("txt_path")
        or fields.get("pdf")
    )

    out["case"] = fields.get("case") or fields.get("case_id")
    out["kind"] = fields.get("kind")

    return out


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
        help="Maximum number of matching results to display.",
    )

    args = parser.parse_args()
    query_text = " ".join(args.query)

    print(f"🔎 Searching for: {query_text!r}")
    if args.case:
        print(f"   (case filter: {args.case})")
    if args.kind:
        print(f"   (kind filter: {args.kind})")
    print()

    # IMPORTANT: run_search expects query= (string), not query_text= or a list
    results_raw = run_search(
        query=query_text,
        limit=args.limit,
        case=args.case,
        kind=args.kind,
    )

    if not results_raw:
        print("No results found.")
        return

    for hit in results_raw:
        r = _coerce_hit(hit)

        meta_bits: List[str] = []
        if r["case"]:
            meta_bits.append(f"case={r['case']}")
        if r["kind"]:
            meta_bits.append(f"kind={r['kind']}")
        meta_str = f" [{' '.join(meta_bits)}]" if meta_bits else ""

        score_str = f"{r['score']:.3f}" if isinstance(r["score"], (int, float)) else "n/a"

        print(f"📄 {r['doc_id'] or 'UNKNOWN_DOC'}  (score={score_str}){meta_str}")
        if r["source"]:
            print(f"    Source: {r['source']}")
        print("-" * 80)
        if r["snippet"]:
            print(r["snippet"])
        else:
            print("(no snippet available)")
        print()


if __name__ == "__main__":
    main()
