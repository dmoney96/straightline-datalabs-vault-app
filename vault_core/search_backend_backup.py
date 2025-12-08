#!/usr/bin/env python
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import sys
from whoosh.index import open_dir
from whoosh.qparser import QueryParser
from whoosh import scoring

from paths import OUTPUT_DIR
from vault_core.manifest import iter_manifest  # type: ignore[import]

INDEX_DIR = OUTPUT_DIR / "index"


def build_manifest_index() -> dict[Path, dict]:
    """
    Build a mapping from absolute TXT path -> manifest record.
    """
    index: dict[Path, dict] = {}

    entries = list(iter_manifest() or [])
    for rec in entries:
        txt = rec.get("txt")
        if not txt:
            continue
        try:
            p = Path(txt).resolve()
        except Exception:
            continue
        index[p] = rec

    return index


def run_search(
    query_text: str,
    case: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Run a search against the Whoosh index and return a list of result dicts:

    {
      "doc_id": str,
      "source": str,
      "score": float,
      "case": str | None,
      "kind": str | None,
      "snippet": str,
    }
    """
    manifest_index = build_manifest_index()
    ix = open_dir(INDEX_DIR)

    results_out: List[Dict[str, Any]] = []

    with ix.searcher(weighting=scoring.BM25F()) as searcher:
        parser = QueryParser("content", schema=ix.schema)
        query = parser.parse(query_text)

        results = searcher.search(query, limit=None)

        for hit in results:
            source = hit.get("source_file", "")
            source_path = None
            hit_case = None
            hit_kind = None

            if source:
                try:
                    source_path = Path(source).resolve()
                except Exception:
                    source_path = None

            if source_path is not None:
                rec = manifest_index.get(source_path)
                if rec:
                    hit_case = rec.get("case")
                    hit_kind = rec.get("kind")

            # Apply filters
            if case is not None and (hit_case or None) != case:
                continue
            if kind is not None and (hit_kind or None) != kind:
                continue

            # Build snippet
            try:
                snippet = hit.highlights("content", top=3)
            except KeyError:
                snippet = (
                    "(content not stored in index; search worked, "
                    "but no snippet is available with current schema)"
                )

            if not snippet:
                snippet = (hit.get("content", "") or "")[:300]

            results_out.append(
                {
                    "doc_id": hit.get("doc_id", "unknown"),
                    "source": source,
                    "score": float(hit.score),
                    "case": hit_case,
                    "kind": hit_kind,
                    "snippet": snippet,
                }
            )

            if len(results_out) >= limit:
                break

    return results_out

def index_txt_document(
    txt_path: str,
    case: str | None = None,
    kind: str | None = None,
) -> None:
    """
    Index a plain-text document into the Whoosh index.

    - txt_path: path to a .txt file
    - case: optional case tag (e.g. "epstein_test2")
    - kind: optional kind tag (e.g. "web_html")
    """
    txt_path = str(Path(txt_path).resolve())
    p = Path(txt_path)

    # Adjusted to match INDEX_DIR as defined in this file -- will revist as necessary
    ix = open_dir(INDEX_DIR)

    content = p.read_text(encoding="utf-8", errors="replace")
    doc_id = p.stem

    writer = ix.writer()
    writer.update_document(
        doc_id=doc_id,
        source=txt_path,
        content=content,
        case=case,
        kind=kind or "web_text",
    )
    writer.commit()
   # This file has been adjusted to help stop hanging searches in web UI
