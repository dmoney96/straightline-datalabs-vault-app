from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

from whoosh import index
from whoosh.fields import ID, TEXT, Schema
from whoosh.qparser import MultifieldParser, OrGroup

from vault_core.paths import INDEX_DIR

log = logging.getLogger(__name__)


def _get_schema() -> Schema:
    """Define the Whoosh schema for our vault index."""
    return Schema(
        doc_id=ID(stored=True, unique=True),
        source_file=ID(stored=True),
        content=TEXT(stored=True),
    )


def _get_or_create_index():
    """
    Open the existing index, or create a new one if it doesn't exist yet.
    """
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    if not index.exists_in(INDEX_DIR):
        log.info("Creating new search index in %s", INDEX_DIR)
        return index.create_in(INDEX_DIR, _get_schema())

    return index.open_dir(INDEX_DIR)


def update_index_for_file(txt_path: Path) -> None:
    """
    Add or update a document in the index from a given TXT file path.
    """
    txt_path = Path(txt_path)
    ix = _get_or_create_index()

    content = txt_path.read_text(encoding="utf-8", errors="ignore")
    doc_id = txt_path.stem

    with ix.writer() as writer:
        writer.update_document(
            doc_id=doc_id,
            source_file=str(txt_path),
            content=content,
        )

    log.info("Updated index for: %s", txt_path)


def search_index(query: str, limit: int = 10) -> List[Dict]:
    """
    Run a full-text search against the index and return a list of hits.

    Each hit is a dict:
        {
            "score": float,
            "doc_id": str,
            "source_file": str,
            "snippet": str,   # HTML with <b class="match term0">…</b>
        }
    """
    ix = _get_or_create_index()

    with ix.searcher() as searcher:
        parser = MultifieldParser(
            ["content"],
            schema=ix.schema,
            group=OrGroup,
        )
        q = parser.parse(query)
        results = searcher.search(q, limit=limit)

        hits: List[Dict] = []
        for hit in results:
            snippet = hit.highlights("content") or ""
            hits.append(
                {
                    "score": float(hit.score),
                    "doc_id": hit["doc_id"],
                    "source_file": hit["source_file"],
                    "snippet": snippet,
                }
            )

    return hits


__all__ = ["update_index_for_file", "search_index"]
